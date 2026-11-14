import XCTest
@testable import LabyrinthKit

final class UpdaterPolicyTests: XCTestCase {
    func testNoKeyDisablesUpdates() {
        XCTAssertFalse(updaterEnabled(publicKey: nil, fixture: false))
        XCTAssertFalse(updaterEnabled(publicKey: "", fixture: false))
        XCTAssertFalse(updaterEnabled(publicKey: "  \n", fixture: false))
    }
    func testFixtureDisablesUpdates() {
        XCTAssertFalse(updaterEnabled(publicKey: "abc=", fixture: true))
    }
    func testKeyAndRealRunEnablesUpdates() {
        XCTAssertTrue(updaterEnabled(publicKey: "abc=", fixture: false))
    }
}
