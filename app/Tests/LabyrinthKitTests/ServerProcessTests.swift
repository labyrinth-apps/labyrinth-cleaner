import XCTest
import Darwin
@testable import LabyrinthKit

final class ServerProcessTests: XCTestCase {
    let fm = FileManager.default
    var home = ""
    var live: [ServerProcess] = []

    override func setUpWithError() throws {
        let raw = fm.temporaryDirectory.appendingPathComponent("lc-srv-\(UUID().uuidString)").path
        try fm.createDirectory(atPath: raw, withIntermediateDirectories: true)
        home = realpathString(raw)!
    }

    override func tearDownWithError() throws {
        let sem = DispatchSemaphore(value: 0)
        let procs = live
        DispatchQueue.global().async {
            for p in procs {
                let g = DispatchSemaphore(value: 0)
                p.stop(grace: 5) { g.signal() }
                g.wait()
            }
            sem.signal()
        }
        // stop calls completion on main, so spin the run loop
        while sem.wait(timeout: .now()) == .timedOut {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        live = []
        try? fm.removeItem(atPath: home)
    }

    func repoDevclean() -> String {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<4 { url.deleteLastPathComponent() }   // Tests/LabyrinthKitTests/X.swift -> repo root
        return url.appendingPathComponent("devclean").path
    }

    func makeServer(script: String? = nil, args: [String]? = nil, timeout: TimeInterval = 30) -> ServerProcess {
        let script = script ?? repoDevclean()
        let launch = ServerLaunch(python: "/usr/bin/python3", script: script,
            arguments: args ?? ["-E", "-s", "-X", "utf8", script, "ui", "--app", "--home", home])
        var env = serverEnvironment(base: ProcessInfo.processInfo.environment, home: home,
                                    helperPath: "/nonexistent/labyrinth-trash")
        env["HOME"] = home
        let s = ServerProcess(launch: launch, environment: env, handshakeTimeout: timeout)
        live.append(s)
        return s
    }

    func startSync(_ s: ServerProcess) -> Result<ServerHandshake, ServerFailure> {
        let e = expectation(description: "start")
        var out: Result<ServerHandshake, ServerFailure>!
        s.start { out = $0; e.fulfill() }
        wait(for: [e], timeout: 40)
        return out
    }

    func children() -> [String] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        p.arguments = ["-f", home]
        let pipe = Pipe(); p.standardOutput = pipe
        try? p.run(); p.waitUntilExit()
        let s = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return s.split(separator: "\n").map(String.init)
    }

    func testStartsAndHandshakes() throws {
        let s = makeServer()
        guard case .success(let h) = startSync(s) else { return XCTFail("start failed") }
        let token = URLComponents(url: h.url, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "t" }!.value!
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(h.port)/api/settings")!)
        req.setValue(token, forHTTPHeaderField: "X-Devclean-Token")
        let e = expectation(description: "http")
        var code = 0
        URLSession.shared.dataTask(with: req) { _, r, _ in code = (r as? HTTPURLResponse)?.statusCode ?? -1; e.fulfill() }.resume()
        wait(for: [e], timeout: 10)
        XCTAssertEqual(code, 200)
    }

    func testStartTwiceOneProcess() throws {
        let s = makeServer()
        guard case .success(let a) = startSync(s) else { return XCTFail("start failed") }
        guard case .success(let b) = startSync(s) else { return XCTFail("second start failed") }
        XCTAssertEqual(a.port, b.port)
        XCTAssertEqual(children().count, 1)
    }

    func testConcurrentStartsOneProcess() throws {
        let s = makeServer()
        let e1 = expectation(description: "1"), e2 = expectation(description: "2")
        var p1 = 0, p2 = 0
        s.start { if case .success(let h) = $0 { p1 = h.port }; e1.fulfill() }
        s.start { if case .success(let h) = $0 { p2 = h.port }; e2.fulfill() }
        wait(for: [e1, e2], timeout: 40)
        XCTAssertNotEqual(p1, 0)
        XCTAssertEqual(p1, p2)
        XCTAssertEqual(children().count, 1)
    }

    func testStopEndsChild() throws {
        let s = makeServer()
        guard case .success = startSync(s) else { return XCTFail("start failed") }
        let pids = children().compactMap { Int32($0) }
        XCTAssertEqual(pids.count, 1)
        let e = expectation(description: "stop")
        s.stop(grace: 10) { e.fulfill() }
        wait(for: [e], timeout: 20)
        for pid in pids {
            XCTAssertEqual(kill(pid, 0), -1)
            XCTAssertEqual(errno, ESRCH)
        }
    }

    func testStartDuringStopIsRefusedNotTheDyingHandshake() throws {
        let s = makeServer()
        guard case .success = startSync(s) else { return XCTFail("start failed") }
        let stopped = expectation(description: "stop")
        s.stop(grace: 10) { stopped.fulfill() }
        // Asked at once, before the child has exited: must not get the old handshake back.
        switch startSync(s) {
        case .success: XCTFail("start during stop returned the dying server's handshake")
        case .failure(.noHandshake(let tail)): XCTAssertTrue(tail.contains("still stopping"), tail)
        case .failure(let f): XCTFail("unexpected \(f)")
        }
        wait(for: [stopped], timeout: 20)
        // Once it is gone, a fresh start works again.
        guard case .success = startSync(s) else { return XCTFail("restart after stop failed") }
    }

    func testStopWithoutStartCompletes() {
        let s = makeServer()
        let e = expectation(description: "stop")
        s.stop(grace: 1) { e.fulfill() }
        wait(for: [e], timeout: 5)
    }

    func testNoHandshakeReportsStderr() throws {
        let script = home + "/boom.py"
        try "import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n".write(toFile: script, atomically: true, encoding: .utf8)
        let s = makeServer(script: script, args: ["-E", "-s", script])
        switch startSync(s) {
        case .success: XCTFail("should fail")
        case .failure(.exited(let status, let tail)):
            XCTAssertEqual(status, 1)
            XCTAssertTrue(tail.contains("boom"), tail)
        case .failure(let f): XCTFail("unexpected \(f)")
        }
    }

    func testHandshakeTimeout() throws {
        let script = home + "/hang.py"
        try "import sys, time\nsys.stderr.write('waiting\\n')\nsys.stderr.flush()\ntime.sleep(60)\n".write(toFile: script, atomically: true, encoding: .utf8)
        let s = makeServer(script: script, args: ["-E", "-s", script], timeout: 1)
        switch startSync(s) {
        case .success: XCTFail("should fail")
        case .failure(.noHandshake(let tail)): XCTAssertTrue(tail.contains("waiting"), tail)
        case .failure(let f): XCTFail("unexpected \(f)")
        }
    }

    func testSpawnFailureIsReportedAndNeverCrashes() {
        for _ in 0..<50 {
            let launch = ServerLaunch(python: "/nonexistent/python3", script: "x", arguments: ["x"])
            let s = ServerProcess(launch: launch, environment: [:], handshakeTimeout: 5)
            switch startSync(s) {
            case .success: XCTFail("should fail")
            case .failure(.noHandshake(let tail)): XCTAssertTrue(tail.contains("Could not start"), tail)
            case .failure(let f): XCTFail("unexpected \(f)")
            }
        }
    }
}
