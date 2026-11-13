import XCTest
@testable import LabyrinthKit

final class NavigationPolicyTests: XCTestCase {
    func d(_ s: String, _ port: Int = 5000) -> NavDecision { navigationDecision(url: URL(string: s)!, serverPort: port) }

    func testServerURLAllowed() { XCTAssertEqual(d("http://127.0.0.1:5000/?t=x"), .allow) }
    func testOtherPortCancelled() { XCTAssertEqual(d("http://127.0.0.1:5001/"), .cancel) }
    func testFileCancelled() { XCTAssertEqual(d("file:///Users/u/x.html"), .cancel) }
    func testHttpsOpensExternally() { XCTAssertEqual(d("https://example.com"), .openExternally) }
    func testMailtoOpensExternally() { XCTAssertEqual(d("mailto:a@b.c"), .openExternally) }
    func testLocalhostCancelled() { XCTAssertEqual(d("http://localhost:5000"), .cancel) }
    func testAboutBlankCancelled() { XCTAssertEqual(d("about:blank"), .cancel) }
    func testPlainHttpOtherHostCancelled() { XCTAssertEqual(d("http://example.com/"), .cancel) }
}
