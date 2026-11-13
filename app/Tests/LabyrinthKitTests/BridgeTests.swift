import XCTest
@testable import LabyrinthKit

final class BridgeTests: XCTestCase {
    let ok: [String: String] = ["type": "pickProjectFolder"]

    func testValid() {
        XCTAssertEqual(parseBridgeMessage(body: ok, originHost: "127.0.0.1", originPort: 5000, serverPort: 5000), .pickProjectFolder)
    }
    func testRejections() {
        XCTAssertNil(parseBridgeMessage(body: ok, originHost: "evil.com", originPort: 5000, serverPort: 5000))
        XCTAssertNil(parseBridgeMessage(body: ok, originHost: "127.0.0.1", originPort: 5001, serverPort: 5000))
        XCTAssertNil(parseBridgeMessage(body: ["type": "other"], originHost: "127.0.0.1", originPort: 5000, serverPort: 5000))
        XCTAssertNil(parseBridgeMessage(body: ["type": "pickProjectFolder", "x": 1], originHost: "127.0.0.1", originPort: 5000, serverPort: 5000))
        XCTAssertNil(parseBridgeMessage(body: "pickProjectFolder", originHost: "127.0.0.1", originPort: 5000, serverPort: 5000))
        XCTAssertNil(parseBridgeMessage(body: [1, 2], originHost: "127.0.0.1", originPort: 5000, serverPort: 5000))
    }
    func testAddedFolderScript() throws {
        let paths = ["a\"b", "a\\b", "a\nb", "a\u{2028}b", "a\u{2029}b", "</script>", "/Users/u/Developer"]
        let prefix = "window.labyrinthAddedFolder("
        for p in paths {
            let s = addedFolderScript(path: p)
            XCTAssertTrue(s.hasPrefix(prefix), p)
            XCTAssertTrue(s.hasSuffix(");"), p)
            XCTAssertFalse(s.contains("\n"), p)
            XCTAssertFalse(s.contains("\u{2028}"), p)
            XCTAssertFalse(s.contains("\u{2029}"), p)
            XCTAssertFalse(s.contains("</"), p)
            let arg = String(s.dropFirst(prefix.count).dropLast(2))
            let back = try JSONSerialization.jsonObject(with: Data(arg.utf8), options: .fragmentsAllowed) as? String
            XCTAssertEqual(back, p)
        }
    }
}
