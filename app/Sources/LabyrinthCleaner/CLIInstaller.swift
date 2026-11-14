import AppKit
import LabyrinthKit

/// "Install Command Line Tool…": links `devclean` to the launcher inside the app.
enum CLIInstaller {
    static func run(fixture: FixtureOptions? = nil) {
        guard let resources = Bundle.main.resourceURL else { return }
        let readOnly = (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]))?.volumeIsReadOnly ?? false
        if let blocker = cliInstallBlocker(bundlePath: Bundle.main.bundlePath, volumeIsReadOnly: readOnly) {
            return done("Can't install the command line tool yet", blocker)
        }
        let target = resources.appendingPathComponent("core/devclean-launcher").path
        let fm = FileManager.default
        let home = fixture?.home ?? NSHomeDirectory()
        let dirs = cliDestinations(home: home, fixtureHome: fixture?.home,
                                   isWritable: { fm.isWritableFile(atPath: $0) },
                                   exists: { fm.fileExists(atPath: $0) })

        let choose = NSAlert()
        choose.messageText = "Install the command line tool"
        choose.informativeText = "This adds a `devclean` command that runs Labyrinth Cleaner from Terminal. Where should it go?"
        for d in dirs { choose.addButton(withTitle: d.hasPrefix(home) ? "~" + d.dropFirst(home.count) : d) }
        choose.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        let pick = choose.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        guard pick >= 0, pick < dirs.count else { return }
        let dir = dirs[pick]
        let path = dir + "/devclean"

        do {
            if dir.hasPrefix(home) {
                try fm.createDirectory(atPath: dir, withIntermediateDirectories: true,
                                       attributes: [.posixPermissions: 0o755])
            }
            switch planLink(state: linkState(at: path), target: target) {
            case .alreadyInstalled:
                return done("Already installed", "\(path) already points to Labyrinth Cleaner.")
            case .refuse(let message):
                return done("Can't install", message)
            case .create:
                break
            case .replaceOurs:
                try fm.removeItem(atPath: path)
            case .askReplace(let old):
                let ask = NSAlert()
                ask.messageText = "Replace the existing devclean?"
                ask.informativeText = "\(path) points to \(old). Replace it with Labyrinth Cleaner's command line tool?"
                ask.addButton(withTitle: "Replace")
                ask.addButton(withTitle: "Cancel")
                guard ask.runModal() == .alertFirstButtonReturn else { return }
                try fm.removeItem(atPath: path)
            }
            try fm.createSymbolicLink(atPath: path, withDestinationPath: target)
            var message = "Installed \(path)."
            if dir.hasPrefix(home) {
                message += "\n\nIf Terminal says devclean isn't found, add this line to ~/.zshrc:\n\nexport PATH=\"$HOME/.local/bin:$PATH\""
            }
            done("Command line tool installed", message)
        } catch {
            done("Could not install the command line tool", error.localizedDescription)
        }
    }

    private static func done(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}
