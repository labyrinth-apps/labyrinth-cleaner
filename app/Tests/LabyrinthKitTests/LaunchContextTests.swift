import XCTest
import CoreServices
@testable import LabyrinthKit

final class LaunchContextTests: XCTestCase {
    private func event(propData: OSType?) -> NSAppleEventDescriptor {
        let e = NSAppleEventDescriptor.appleEvent(withEventClass: AEEventClass(kCoreEventClass), eventID: AEEventID(kAEOpenApplication),
                                                  targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID))
        if let p = propData { e.setParam(NSAppleEventDescriptor(enumCode: p), forKeyword: AEKeyword(keyAEPropData)) }
        return e
    }
    func testLoginLaunch() { XCTAssertTrue(isLoginLaunch(event(propData: OSType(keyAELaunchedAsLogInItem)))) }
    func testNil() { XCTAssertFalse(isLoginLaunch(nil)) }
    func testNoParam() { XCTAssertFalse(isLoginLaunch(event(propData: nil))) }
    func testOtherValue() { XCTAssertFalse(isLoginLaunch(event(propData: OSType(keyAEPropData)))) }
}
