import Foundation
import UserNotifications
import LabyrinthKit

/// "Disk almost full" notification. Permission is requested only the first time one is due.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var state = NotifyState()
    var disabled = false
    var onClick: () -> Void = {}

    private var center: UNUserNotificationCenter? {
        // UNUserNotificationCenter traps when the process isn't inside an app bundle.
        Bundle.main.bundleIdentifier == nil ? nil : UNUserNotificationCenter.current()
    }

    /// Setting the delegate does not prompt; it only lets a click reach us.
    func install() {
        center?.delegate = self
    }

    func check(freeBytes: Int64, freeText: String) {
        guard !disabled, step(&state, freeBytes: freeBytes), let center else { return }
        center.requestAuthorization(options: [.alert]) { granted, _ in
            guard granted else { return }
            let c = UNMutableNotificationContent()
            c.title = "Disk almost full"
            c.body = "Only \(freeText) free. Open Labyrinth Cleaner to free up space."
            center.add(UNNotificationRequest(identifier: "disk-almost-full", content: c, trigger: nil))
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        DispatchQueue.main.async { self.onClick() }
        completionHandler()
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner])
    }
}
