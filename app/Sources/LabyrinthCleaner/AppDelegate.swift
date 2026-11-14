import AppKit
import LabyrinthKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusMenu = StatusMenu()
    private let notifier = Notifier()
    private var server: ServerProcess?
    private var fixture: FixtureOptions?
    private var window: MainWindowController?
    private var launchedAsLoginItem = false

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openWindow()
        return true
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // The launch Apple event is only available here (it is nil in applicationWillFinishLaunching).
        launchedAsLoginItem = isLoginLaunch(NSAppleEventManager.shared().currentAppleEvent)
        #if DEBUG
        fixture = parseFixtureOptions(CommandLine.arguments)
        #endif
        notifier.disabled = fixture != nil          // fixture runs never ask for or post notifications
        statusMenu.fixture = fixture
        notifier.onClick = { [weak self] in self?.openWindow() }
        notifier.install()
        statusMenu.onOpen = { [weak self] in self?.openWindow() }
        statusMenu.onQuit = { NSApp.terminate(nil) }
        statusMenu.onReading = { [weak self] free, text in self?.notifier.check(freeBytes: free, freeText: text) }
        statusMenu.install()
        installMainMenu()
        if fixture == nil { warnAboutLegacyBarOnce() }   // fixture runs must not touch real defaults
        if !launchedAsLoginItem { openWindow() }
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
        s.onUnexpectedExit = { [weak self] failure in
            guard let self else { return }
            let wasVisible = self.window?.isVisible ?? false
            self.window?.forgetLoadedServer()
            if wasVisible { self.show(failure, title: "Labyrinth Cleaner's scanner stopped") }
        }
        server = s
        return s
    }

    /// Starts the server if needed, loads its page once per server, and shows the window.
    func openWindow() {
        makeServer().start { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let h):
                let w = self.window ?? MainWindowController(startFolder: self.fixture?.home ?? NSHomeDirectory())
                self.window = w
                w.show(handshake: h)
            case .failure(let f): self.show(f, title: "Labyrinth Cleaner couldn't start its scanner")
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
