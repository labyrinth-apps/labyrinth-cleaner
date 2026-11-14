import XCTest
@testable import LabyrinthKit

final class CLILinkTests: XCTestCase {
    private let target = "/Applications/Labyrinth Cleaner.app/Contents/Resources/core/devclean-launcher"

    func testPlans() {
        XCTAssertEqual(planLink(state: .missing, target: target), .create)
        XCTAssertEqual(planLink(state: .symlink(to: target), target: target), .alreadyInstalled)
        XCTAssertEqual(planLink(state: .symlink(to: "/Users/x/Old/Labyrinth Cleaner.app/Contents/Resources/core/devclean-launcher"), target: target),
                       .replaceOurs(old: "/Users/x/Old/Labyrinth Cleaner.app/Contents/Resources/core/devclean-launcher"))
        XCTAssertEqual(planLink(state: .symlink(to: "/Users/x/src/labyrinth-cleaner/devclean"), target: target),
                       .askReplace(old: "/Users/x/src/labyrinth-cleaner/devclean"))
        guard case .refuse(let msg) = planLink(state: .notALink, target: target) else { return XCTFail("expected refuse") }
        XCTAssertEqual(msg, "~/.local/bin/devclean exists and isn't a link. Remove it first.")
    }

    func testDestinations() {
        let home = "/Users/x"
        XCTAssertEqual(cliDestinations(home: home, isWritable: { _ in true }, exists: { _ in true }),
                       ["/Users/x/.local/bin", "/usr/local/bin"])
        XCTAssertEqual(cliDestinations(home: home, isWritable: { _ in false }, exists: { _ in true }),
                       ["/Users/x/.local/bin"])
        XCTAssertEqual(cliDestinations(home: home, isWritable: { _ in true }, exists: { _ in false }),
                       ["/Users/x/.local/bin"])
    }

    func testLinkState() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("cli-link-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let p = dir.appendingPathComponent("devclean").path
        XCTAssertEqual(linkState(at: p), .missing)
        try FileManager.default.createSymbolicLink(atPath: p, withDestinationPath: dir.appendingPathComponent("nowhere").path)
        XCTAssertEqual(linkState(at: p), .symlink(to: dir.appendingPathComponent("nowhere").path))   // dangling
        try FileManager.default.removeItem(atPath: p)
        FileManager.default.createFile(atPath: p, contents: Data())
        XCTAssertEqual(linkState(at: p), .notALink)
    }
}
