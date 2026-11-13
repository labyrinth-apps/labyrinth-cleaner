import XCTest
@testable import LabyrinthKit

final class FreeSpaceTests: XCTestCase {
    func testLevels() {
        let GB = bytesPerGB
        XCTAssertEqual(level(freeBytes: 15 * GB - 1), .red)
        XCTAssertEqual(level(freeBytes: 15 * GB), .amber)
        XCTAssertEqual(level(freeBytes: 30 * GB - 1), .amber)
        XCTAssertEqual(level(freeBytes: 30 * GB), .normal)
        XCTAssertEqual(level(freeBytes: 0), .red)
    }

    func testNotifySequence() {
        var s = NotifyState()
        var got: [Bool] = []
        for r: Int64 in [40, 14, 10, 18, 21, 14] { got.append(step(&s, freeBytes: r * bytesPerGB)) }
        XCTAssertEqual(got, [false, true, false, false, false, true])
    }

    func testExactlyTwentyDoesNotRearm() {
        var t = NotifyState()
        _ = step(&t, freeBytes: 14 * bytesPerGB)
        XCTAssertFalse(step(&t, freeBytes: 20 * bytesPerGB))
        XCTAssertFalse(step(&t, freeBytes: 14 * bytesPerGB))
    }

    func testFormat() {
        let text = formatFree(57_100_000_000)
        XCTAssertTrue(text.hasSuffix("GB") && text.hasPrefix("57"), text)
    }
}
