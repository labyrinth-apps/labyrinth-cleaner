import AppKit
import ServiceManagement
import LabyrinthKit

/// The menu-bar item: the free-space number and its menu.
final class StatusMenu: NSObject, NSMenuDelegate, NSMenuItemValidation {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let infoItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let loginItem = NSMenuItem(title: "Open at Login", action: nil, keyEquivalent: "")
    private var timer: Timer?
    /// Set by showQuitting(); refresh() then leaves the title alone.
    private var quitting = false

    /// Set in fixture runs: nothing here may touch the real login items, folders or notifications.
    var fixture: FixtureOptions?
    var onOpen: () -> Void = {}
    var onQuit: () -> Void = {}
    var onCheckForUpdates: () -> Void = {}
    var canCheckForUpdates: () -> Bool = { false }
    private let updatesItem = NSMenuItem(title: "Check for Updates…", action: nil, keyEquivalent: "")
    /// Called with every reading so the notifier can decide.
    var onReading: (_ free: Int64, _ text: String) -> Void = { _, _ in }

    func install() {
        infoItem.isEnabled = false
        menu.addItem(infoItem)
        menu.addItem(.separator())
        let open = NSMenuItem(title: "Open Labyrinth Cleaner", action: #selector(openTapped), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        loginItem.action = #selector(toggleLogin)
        loginItem.target = self
        menu.addItem(loginItem)
        updatesItem.action = #selector(updatesTapped)
        updatesItem.target = self
        menu.addItem(updatesItem)
        let cli = NSMenuItem(title: "Install Command Line Tool…", action: #selector(cliTapped), keyEquivalent: "")
        cli.target = self
        menu.addItem(cli)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Labyrinth Cleaner", action: #selector(quitTapped), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        menu.delegate = self
        statusItem.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.refresh() }
    }

    /// autoenablesItems would re-enable any item whose target responds to its action, so decide here.
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item === updatesItem ? canCheckForUpdates() : true
    }

    func menuWillOpen(_ menu: NSMenu) {
        refresh()
        loginItem.state = fixture == nil && SMAppService.mainApp.status == .enabled ? .on : .off
    }

    /// Free space as Finder reports it (includes space macOS can purge by itself).
    private func readSpace() -> (free: Int64, total: Int64)? {
        let keys: Set<URLResourceKey> = [.volumeAvailableCapacityForImportantUsageKey, .volumeTotalCapacityKey]
        guard let v = try? URL(fileURLWithPath: "/").resourceValues(forKeys: keys),
              let free = v.volumeAvailableCapacityForImportantUsage,
              let total = v.volumeTotalCapacity else { return nil }
        return (free, Int64(total))
    }

    /// Decision 6: while Quit waits for the server to stop, the menu bar reads "Quitting…".
    func showQuitting() {
        quitting = true
        timer?.invalidate()
        timer = nil
        statusItem.button?.title = "Quitting…"
        statusItem.button?.toolTip = "Labyrinth Cleaner is quitting"
    }

    func refresh() {
        guard !quitting else { return }
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
        statusItem.button?.toolTip = "Free disk space (Labyrinth Cleaner)"
        infoItem.title = "\(text) free of \(formatFree(space.total))"
        onReading(space.free, text)
    }

    @objc private func openTapped() { onOpen() }
    @objc private func quitTapped() { onQuit() }
    @objc private func updatesTapped() { onCheckForUpdates() }
    @objc private func cliTapped() { CLIInstaller.run(fixture: fixture) }

    @objc private func toggleLogin() {
        if fixture != nil {
            let alert = NSAlert()
            alert.messageText = fixtureLoginMessage
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
            loginItem.state = .off
            return
        }
        let service = SMAppService.mainApp
        do {
            switch service.status {
            case .enabled: try service.unregister()
            case .requiresApproval: SMAppService.openSystemSettingsLoginItems()
            default: try service.register()
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = "Could not change Open at Login"
            alert.informativeText = error.localizedDescription
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
        }
        loginItem.state = service.status == .enabled ? .on : .off
    }
}
