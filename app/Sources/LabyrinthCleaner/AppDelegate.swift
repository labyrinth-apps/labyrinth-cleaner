import AppKit
import LabyrinthKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusMenu = StatusMenu()
    private let notifier = Notifier()
    private var server: ServerProcess?
    private var fixture: FixtureOptions?

    func applicationDidFinishLaunching(_ notification: Notification) {
        #if DEBUG
        fixture = parseFixtureOptions(CommandLine.arguments)
        #endif
        notifier.onClick = { [weak self] in self?.openWindow() }
        notifier.install()
        statusMenu.onOpen = { [weak self] in self?.openWindow() }
        statusMenu.onQuit = { NSApp.terminate(nil) }
        statusMenu.onReading = { [weak self] free, text in self?.notifier.check(freeBytes: free, freeText: text) }
        statusMenu.install()
        if fixture == nil { warnAboutLegacyBarOnce() }   // fixture runs must not touch real defaults
    }

    private func warnAboutLegacyBarOnce() {
        let key = "legacyBarWarned"
        let ids = NSWorkspace.shared.runningApplications.compactMap(\.bundleIdentifier)
        guard legacyBarRunning(bundleIDs: ids), !UserDefaults.standard.bool(forKey: key) else { return }
        UserDefaults.standard.set(true, forKey: key)
        let alert = NSAlert()
        alert.messageText = "DevcleanBar is still running"
        alert.informativeText = "The older free-space item DevcleanBar is still running, so you'll see two numbers in the menu bar. To remove it, run this in Terminal:\n\ndevclean menubar uninstall"
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    private func makeServer() -> ServerProcess {
        if let s = server { return s }
        let resources = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let home = fixture?.home ?? NSHomeDirectory()
        let helper = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/labyrinth-trash").path
        let env = serverEnvironment(base: ProcessInfo.processInfo.environment, home: home, helperPath: helper)
        let s = ServerProcess(launch: serverLaunch(resources: resources, fixture: fixture), environment: env)
        s.onUnexpectedExit = { [weak self] failure in self?.show(failure, title: "Labyrinth Cleaner's server stopped") }
        server = s
        return s
    }

    /// Task 5 puts the window here; for now it starts the server and logs the port.
    func openWindow() {
        makeServer().start { [weak self] result in
            switch result {
            case .success(let h): NSLog("Labyrinth Cleaner server on port \(h.port)")
            case .failure(let f): self?.show(f, title: "Labyrinth Cleaner couldn't start")
            }
        }
    }

    private func show(_ failure: ServerFailure, title: String) {
        let tail: String
        switch failure {
        case .noHandshake(let t): tail = t
        case .exited(let status, let t): tail = "Exit status \(status).\n" + t
        }
        let lines = tail.split(separator: "\n", omittingEmptySubsequences: true).suffix(8).joined(separator: "\n")
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = lines.isEmpty ? "No details were reported." : lines
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let server else { return .terminateNow }
        server.stop { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}
