import Foundation
import CoreServices

/// true when the event is the open-application event and the launch Apple event carries keyAEPropData == keyAELaunchedAsLogInItem.
public func isLoginLaunch(_ event: NSAppleEventDescriptor?) -> Bool {
    guard let event, event.eventID == AEEventID(kAEOpenApplication) else { return false }
    return event.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
}
