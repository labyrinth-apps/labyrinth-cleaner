import XCTest
@testable import LabyrinthKit

final class ServerHandshakeTests: XCTestCase {
    func testHandshakeAcceptsValid() {
        let h = parseHandshake(#"{"labyrinth_server":1,"url":"http://127.0.0.1:5123/?t=abc"}"#)
        XCTAssertEqual(h?.port, 5123)
        XCTAssertEqual(h?.url.absoluteString, "http://127.0.0.1:5123/?t=abc")
    }

    func testHandshakeRejects() {
        let bad = [
            #"{"labyrinth_server":1,"url":"https://127.0.0.1:5123/?t=abc"}"#,
            #"{"labyrinth_server":1,"url":"http://localhost:5123/?t=abc"}"#,
            #"{"labyrinth_server":1,"url":"http://evil.com:5123/?t=abc"}"#,
            #"{"labyrinth_server":1,"url":"http://127.0.0.1/?t=abc"}"#,
            #"{"labyrinth_server":1,"url":"http://127.0.0.1:5123/"}"#,
            #"{"labyrinth_server":1,"url":"http://127.0.0.1:5123/?t="}"#,
            #"{"labyrinth_server":2,"url":"http://127.0.0.1:5123/?t=abc"}"#,
            #"{"url":"http://127.0.0.1:5123/?t=abc"}"#,
            #"[1]"#,
            "not json",
            "",
        ]
        for line in bad { XCTAssertNil(parseHandshake(line), line) }
    }
}
