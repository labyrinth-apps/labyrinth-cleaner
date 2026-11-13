import AppKit

// DevcleanBar: shows free disk space in the menu bar. It never scans or deletes;
// "Clean up…" runs `devclean ui`, which opens (or reuses) the devclean page.
//
//   DevcleanBar --devclean /path/to/devclean

/// Thread-safe, size-capped buffer for a child's stderr.
final class TailBuffer: @unchecked Sendable {
    private var data = Data()
    private let lock = NSLock()

    func append(_ d: Data) {
        lock.lock()
        data.append(d)
        if data.count > 64 * 1024 { data = data.suffix(64 * 1024) }
        lock.unlock()
    }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return String(decoding: data, as: UTF8.self)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let devclean: String
    var statusItem: NSStatusItem!
    let menu = NSMenu()
    let infoItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var notifyState = NotifyState()
    var timer: Timer?

    init(devclean: String) {
        self.devclean = devclean
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        infoItem.isEnabled = false
        menu.addItem(infoItem)
        menu.addItem(.separator())
        let clean = NSMenuItem(title: "Clean up…", action: #selector(cleanUp), keyEquivalent: "")
        clean.target = self
        menu.addItem(clean)
        let quit = NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        menu.delegate = self
        statusItem.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        refresh()
    }

    /// Free space as Finder reports it (includes space macOS can purge by itself).
    func readSpace() -> (free: Int64, total: Int64)? {
        let keys: Set<URLResourceKey> = [.volumeAvailableCapacityForImportantUsageKey, .volumeTotalCapacityKey]
        guard let v = try? URL(fileURLWithPath: "/").resourceValues(forKeys: keys),
              let free = v.volumeAvailableCapacityForImportantUsage,
              let total = v.volumeTotalCapacity else { return nil }
        return (free, Int64(total))
    }

    func refresh() {
        guard let space = readSpace() else {
            statusItem.button?.title = "? GB"
            infoItem.title = "Free space unavailable"
            return
        }
        let text = formatFree(space.free)
        let color: NSColor
        switch level(freeBytes: space.free) {
        case .normal: color = .labelColor
        case .amber: color = .systemOrange
        case .red: color = .systemRed
        }
        statusItem.button?.attributedTitle = NSAttributedString(string: text, attributes: [
            .foregroundColor: color,
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .medium),
        ])
        statusItem.button?.toolTip = "Free disk space (devclean)"
        infoItem.title = "\(text) free of \(formatFree(space.total))"
        if step(&notifyState, freeBytes: space.free) {
            notify(free: text)
        }
    }

    func notify(free: String) {
        // osascript avoids the signing and permission setup UNUserNotificationCenter needs.
        // The message goes in as an argument, so nothing needs quoting.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = [
            "-e", "on run argv",
            "-e", "display notification (item 1 of argv) with title \"Disk almost full\"",
            "-e", "end run",
            "Only \(free) free. Click the menu bar item → Clean up…",
        ]
        try? p.run()
    }

    @objc func cleanUp() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: devclean)
        p.arguments = ["ui"]
        p.standardOutput = FileHandle.nullDevice
        // Keep the tail of stderr so a failed start can be shown instead of vanishing.
        let errPipe = Pipe()
        let errLog = TailBuffer()
        errPipe.fileHandleForReading.readabilityHandler = { h in errLog.append(h.availableData) }
        p.standardError = errPipe
        p.terminationHandler = { proc in
            errPipe.fileHandleForReading.readabilityHandler = nil
            errLog.append(errPipe.fileHandleForReading.readDataToEndOfFile())
            let msg = launchFailureMessage(status: proc.terminationStatus,
                                           signaled: proc.terminationReason == .uncaughtSignal,
                                           stderr: errLog.text)
            guard let msg else { return }
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.messageText = "devclean couldn't open the page"
                alert.informativeText = msg
                NSApp.activate(ignoringOtherApps: true)
                alert.runModal()
            }
        }
        do {
            try p.run()
        } catch {
            let alert = NSAlert()
            alert.messageText = "Could not start devclean"
            alert.informativeText = "\(devclean): \(error.localizedDescription)\nRun `devclean menubar install` again."
            alert.runModal()
        }
    }

    @objc func quit() {
        NSApp.terminate(nil)
    }
}

var devcleanPath = "devclean"
let args = CommandLine.arguments
if let i = args.firstIndex(of: "--devclean"), i + 1 < args.count {
    devcleanPath = args[i + 1]
}

let app = NSApplication.shared
let delegate = AppDelegate(devclean: devcleanPath)
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
