import Foundation
import CoreServices

/// true when the launch Apple event carries keyAEPropData == keyAELaunchedAsLogInItem.
public func isLoginLaunch(_ event: NSAppleEventDescriptor?) -> Bool {
    event?.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
}
