import Foundation
import Darwin

public enum ServerFailure: Error {
    case noHandshake(stderrTail: String)
    case exited(status: Int32, stderrTail: String)
}

/// Thread-safe, size-capped buffer for a child's stderr.
final class TailBuffer: @unchecked Sendable {
    private var data = Data()
    private let lock = NSLock()
    let limit: Int
    init(limit: Int = 64 * 1024) { self.limit = limit }

    func append(_ d: Data) {
        lock.lock()
        data.append(d)
        if data.count > limit { data = data.suffix(limit) }
        lock.unlock()
    }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return String(decoding: data, as: UTF8.self)
    }
}

/// Owns one `devclean ui --app` child. The child exits by itself when our pipe to its stdin closes,
/// so a crashed app can't leave a server behind.
public final class ServerProcess {
    private enum State {
        case idle
        case starting(waiters: [(Result<ServerHandshake, ServerFailure>) -> Void])
        case running(ServerHandshake)
    }

    private let launch: ServerLaunch
    private let environment: [String: String]
    private let handshakeTimeout: TimeInterval
    private let q = DispatchQueue(label: "labyrinth.server-process")

    // All of the below is touched only on `q`.
    private var state = State.idle
    private var process: Process?
    private var stdinHandle: FileHandle?
    private var stderrTail = TailBuffer()
    private var stopping = false
    private var stopWaiters: [() -> Void] = []
    private var generation = 0

    public var onUnexpectedExit: ((ServerFailure) -> Void)?

    public init(launch: ServerLaunch, environment: [String: String], handshakeTimeout: TimeInterval = 30) {
        self.launch = launch
        self.environment = environment
        self.handshakeTimeout = handshakeTimeout
    }

    /// Starts once; later calls while starting or running share that one child. Completions run on main.
    public func start(completion: @escaping (Result<ServerHandshake, ServerFailure>) -> Void) {
        q.async { self.startLocked(completion) }
    }

    private func startLocked(_ completion: @escaping (Result<ServerHandshake, ServerFailure>) -> Void) {
        switch state {
        case .running(let h):
            DispatchQueue.main.async { completion(.success(h)) }
            return
        case .starting(let waiters):
            state = .starting(waiters: waiters + [completion])
            return
        case .idle:
            break
        }
        if process != nil {
            // A previous child is still shutting down; don't overlap two servers.
            DispatchQueue.main.async { completion(.failure(.noHandshake(stderrTail: "The previous server is still stopping."))) }
            return
        }

        generation += 1
        let gen = generation
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launch.python)
        p.arguments = launch.arguments
        p.environment = environment
        p.currentDirectoryURL = URL(fileURLWithPath: "/")
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = errPipe
        let tail = TailBuffer()
        stderrTail = tail
        stopping = false

        let readers = DispatchGroup()
        readers.enter(); readers.enter()   // entered before run() so a fast exit still waits for both readers
        p.terminationHandler = { [weak self] proc in
            _ = readers.wait(timeout: .now() + 2)
            let status = proc.terminationStatus
            self?.q.async { self?.childExited(status: status, generation: gen) }
        }

        do {
            try p.run()
        } catch {
            readers.leave(); readers.leave()
            // No reader threads exist yet; the pipes are released with their Pipe objects.
            try? inPipe.fileHandleForWriting.close()
            try? outPipe.fileHandleForWriting.close()
            try? errPipe.fileHandleForWriting.close()
            let msg = "Could not start \(launch.python): \(error.localizedDescription)"
            DispatchQueue.main.async { completion(.failure(.noHandshake(stderrTail: msg))) }
            return
        }
        // The parent's copies of the child's ends must be closed or EOF never arrives.
        try? outPipe.fileHandleForWriting.close()
        try? errPipe.fileHandleForWriting.close()
        try? inPipe.fileHandleForReading.close()
        Thread.detachNewThread {
            let h = errPipe.fileHandleForReading
            while true {
                let d = h.availableData
                if d.isEmpty { break }
                tail.append(d)
            }
            readers.leave()
        }
        Thread.detachNewThread { [weak self] in
            let h = outPipe.fileHandleForReading
            var buf = Data()
            var gotFirst = false
            while true {
                let d = h.availableData
                if d.isEmpty { break }
                if gotFirst { continue }   // keep draining so the child never blocks on a full pipe
                buf.append(d)
                if let nl = buf.firstIndex(of: 0x0A) {
                    gotFirst = true
                    let line = String(decoding: buf[buf.startIndex..<nl], as: UTF8.self)
                    self?.q.async { self?.gotFirstLine(line, generation: gen) }
                    buf = Data()
                }
            }
            readers.leave()
        }

        process = p
        stdinHandle = inPipe.fileHandleForWriting
        state = .starting(waiters: [completion])

        q.asyncAfter(deadline: .now() + handshakeTimeout) { [weak self] in
            guard let self, self.generation == gen, case .starting(let waiters) = self.state else { return }
            self.state = .idle
            let failure = ServerFailure.noHandshake(stderrTail: self.stderrTail.text)
            self.fail(waiters, failure)
            if let p = self.process, p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    private func fail(_ waiters: [(Result<ServerHandshake, ServerFailure>) -> Void], _ f: ServerFailure) {
        DispatchQueue.main.async { for w in waiters { w(.failure(f)) } }
    }

    private func gotFirstLine(_ line: String, generation gen: Int) {
        guard gen == generation, case .starting(let waiters) = state else { return }
        if let h = parseHandshake(line) {
            state = .running(h)
            DispatchQueue.main.async { for w in waiters { w(.success(h)) } }
        } else {
            state = .idle
            fail(waiters, .noHandshake(stderrTail: "Unexpected first line from the server: \(line)\n" + stderrTail.text))
            if let p = process, p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    private func childExited(status: Int32, generation gen: Int) {
        guard gen == generation else { return }
        let wasStopping = stopping
        let tail = stderrTail.text
        let prior = state
        state = .idle
        process = nil
        try? stdinHandle?.close()
        stdinHandle = nil
        stopping = false
        switch prior {
        case .starting(let waiters):
            fail(waiters, .exited(status: status, stderrTail: tail))
        case .running:
            if !wasStopping, let cb = onUnexpectedExit {
                DispatchQueue.main.async { cb(.exited(status: status, stderrTail: tail)) }
            }
        case .idle:
            break   // already failed (timeout or bad first line)
        }
        let waiters = stopWaiters
        stopWaiters = []
        if !waiters.isEmpty { DispatchQueue.main.async { for w in waiters { w() } } }
    }

    /// Closes stdin (the server then finishes its current delete and exits by itself), and
    /// SIGKILLs it only if it is still alive after `grace` s. No SIGTERM from here: a second
    /// SIGTERM could cut a running delete short. Calls completion on main.
    public func stop(grace: TimeInterval = 30, completion: @escaping () -> Void) {
        q.async {
            guard let p = self.process else {
                DispatchQueue.main.async { completion() }
                return
            }
            self.stopWaiters.append(completion)
            if case .starting(let waiters) = self.state {
                self.state = .idle
                self.fail(waiters, .noHandshake(stderrTail: self.stderrTail.text))
            }
            if self.stopping { return }
            self.stopping = true
            if case .running = self.state { self.state = .idle }   // a start() now sees "still stopping", not the dying server
            let gen = self.generation
            try? self.stdinHandle?.close()   // EOF: the server finishes its current delete, then exits
            self.q.asyncAfter(deadline: .now() + grace) { [weak self] in
                guard let self, self.generation == gen, let p = self.process, p.isRunning else { return }
                kill(p.processIdentifier, SIGKILL)
            }
        }
    }
}
