import XCTest
import Darwin
@testable import LabyrinthKit

final class TrashCheckTests: XCTestCase {
    var home = ""
    let fm = FileManager.default

    override func setUpWithError() throws {
        let raw = fm.temporaryDirectory.appendingPathComponent("lc-trash-\(UUID().uuidString)").path
        try fm.createDirectory(atPath: raw, withIntermediateDirectories: true)
        home = realpathString(raw)!
    }

    override func tearDownWithError() throws {
        try? fm.removeItem(atPath: home)
    }

    // MARK: helpers

    func p(_ rel: String) -> String { home + "/" + rel }

    @discardableResult
    func mkfile(_ rel: String, _ text: String = "x") throws -> String {
        let path = p(rel)
        try fm.createDirectory(atPath: (path as NSString).deletingLastPathComponent,
                               withIntermediateDirectories: true)
        try text.write(toFile: path, atomically: false, encoding: .utf8)
        return path
    }

    func mkdir(_ rel: String) throws {
        try fm.createDirectory(atPath: p(rel), withIntermediateDirectories: true)
    }

    func run(_ path: String, realHome: String = "/nonexistent-real-home",
             trash: (URL) throws -> Void = { _ in XCTFail("real trash closure called") })
        -> (code: Int32, message: String) {
        runTrashHelper(arguments: ["--home", home, path], realHome: realHome, trash: trash)
    }

    func assertRefused(_ r: (code: Int32, message: String), file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(r.code, 3, r.message, file: file, line: line)
        XCTAssertTrue(r.message.hasPrefix("REFUSED: "), r.message, file: file, line: line)
    }

    // MARK: Downloads

    func testDownloadsFileMovesIntoFixtureTrash() throws {
        let f = try mkfile("Downloads/x.dmg")
        let r = run(f)
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertFalse(fm.fileExists(atPath: f))
        XCTAssertTrue(fm.fileExists(atPath: p(".Trash/x.dmg")))
    }

    func testOneLevelDownAllowed_TwoLevelsRefused() throws {
        let ok = try mkfile("Downloads/sub/a.zip")
        XCTAssertEqual(run(ok).code, 0)
        let deep = try mkfile("Downloads/sub/deeper/b.zip")
        assertRefused(run(deep))
        XCTAssertTrue(fm.fileExists(atPath: deep))
    }

    func testSymlinkFileRefused() throws {
        try mkdir("Downloads")
        let target = try mkfile("elsewhere/real.txt")
        try fm.createSymbolicLink(atPath: p("Downloads/link.txt"), withDestinationPath: target)
        assertRefused(run(p("Downloads/link.txt")))
        XCTAssertTrue(fm.fileExists(atPath: target))
    }

    func testFileUnderSymlinkedSubdirRefused() throws {
        try mkfile("elsewhere/real.txt")
        try mkdir("Downloads")
        try fm.createSymbolicLink(atPath: p("Downloads/sub"), withDestinationPath: p("elsewhere"))
        assertRefused(run(p("Downloads/sub/real.txt")))
        XCTAssertTrue(fm.fileExists(atPath: p("elsewhere/real.txt")))
    }

    func testSymlinkedDownloadsRefused() throws {
        try mkfile("elsewhere/real.txt")
        try fm.createSymbolicLink(atPath: p("Downloads"), withDestinationPath: p("elsewhere"))
        assertRefused(run(p("Downloads/real.txt")))
        XCTAssertTrue(fm.fileExists(atPath: p("elsewhere/real.txt")))
    }

    func testDirectoryInDownloadsRefused() throws {
        try mkdir("Downloads/adir")
        assertRefused(run(p("Downloads/adir")))
        XCTAssertTrue(fm.fileExists(atPath: p("Downloads/adir")))
    }

    func testMissingFileRefused() throws {
        try mkdir("Downloads")
        assertRefused(run(p("Downloads/nope.dmg")))
    }

    func testOtherTopLevelFolderRefused() throws {
        let f = try mkfile("Documents/a.txt")
        assertRefused(run(f))
        XCTAssertTrue(fm.fileExists(atPath: f))
    }

    // MARK: path shape

    func testOutsideHomeRefused() throws {
        assertRefused(run("/etc/hosts"))
    }

    func testSiblingPrefixOfHomeRefused() throws {
        // "<home>2/Downloads/x" must not count as inside "<home>".
        let sib = home + "2"
        try fm.createDirectory(atPath: sib + "/Downloads", withIntermediateDirectories: true)
        defer { try? fm.removeItem(atPath: sib) }
        try "x".write(toFile: sib + "/Downloads/x.dmg", atomically: false, encoding: .utf8)
        assertRefused(run(sib + "/Downloads/x.dmg"))
    }

    func testDotDotRefused() throws {
        let f = try mkfile("Downloads/x.dmg")
        assertRefused(run(p("Downloads/../Downloads/x.dmg")))
        assertRefused(run(p("Downloads/./x.dmg")))
        assertRefused(run(p("Downloads//x.dmg")))
        assertRefused(run(f + "/"))
        XCTAssertTrue(fm.fileExists(atPath: f))
    }

    func testRelativePathUsage() throws {
        let r = run("Downloads/x.dmg")
        XCTAssertEqual(r.code, 2)
    }

    func testUsageErrors() throws {
        XCTAssertEqual(runTrashHelper(arguments: [], realHome: home, trash: { _ in }).code, 2)
        XCTAssertEqual(runTrashHelper(arguments: ["--home", home], realHome: home, trash: { _ in }).code, 2)
        XCTAssertEqual(runTrashHelper(arguments: ["--home", home, "/a", "/b"], realHome: home, trash: { _ in }).code, 2)
        XCTAssertEqual(runTrashHelper(arguments: ["/a", home, "/b"], realHome: home, trash: { _ in }).code, 2)
        XCTAssertEqual(runTrashHelper(arguments: ["--home", home + "/missing", "/a"], realHome: home, trash: { _ in }).code, 2)
    }

    // MARK: Archives

    func testArchiveDirAllowed() throws {
        try mkdir("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")
        let a = p("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")
        let r = run(a)
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertFalse(fm.fileExists(atPath: a))
        XCTAssertTrue(fm.fileExists(atPath: p(".Trash/App.xcarchive")))
    }

    func testArchiveAsFileRefused() throws {
        let f = try mkfile("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")
        assertRefused(run(f))
        XCTAssertTrue(fm.fileExists(atPath: f))
    }

    func testArchiveTooDeepRefused() throws {
        try mkdir("Library/Developer/Xcode/Archives/2026-01-01/sub/App.xcarchive")
        assertRefused(run(p("Library/Developer/Xcode/Archives/2026-01-01/sub/App.xcarchive")))
    }

    func testArchiveWrongSuffixRefused() throws {
        try mkdir("Library/Developer/Xcode/Archives/2026-01-01/App.bundle")
        assertRefused(run(p("Library/Developer/Xcode/Archives/2026-01-01/App.bundle")))
    }

    func testArchiveSymlinkRefused() throws {
        try mkdir("elsewhere/App.xcarchive")
        try mkdir("Library/Developer/Xcode/Archives/2026-01-01")
        try fm.createSymbolicLink(atPath: p("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive"),
                                  withDestinationPath: p("elsewhere/App.xcarchive"))
        assertRefused(run(p("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")))
        XCTAssertTrue(fm.fileExists(atPath: p("elsewhere/App.xcarchive")))
    }

    func testArchiveUnderSymlinkedDateDirRefused() throws {
        try mkdir("elsewhere/App.xcarchive")
        try mkdir("Library/Developer/Xcode/Archives")
        try fm.createSymbolicLink(atPath: p("Library/Developer/Xcode/Archives/2026-01-01"),
                                  withDestinationPath: p("elsewhere"))
        assertRefused(run(p("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")))
        XCTAssertTrue(fm.fileExists(atPath: p("elsewhere/App.xcarchive")))
    }

    // MARK: which Trash

    func testRealHomeUsesTrashClosure() throws {
        let f = try mkfile("Downloads/x.dmg")
        var got: [URL] = []
        let r = run(f, realHome: home, trash: { got.append($0) })
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertEqual(got.map { $0.path }, [f])
        XCTAssertTrue(fm.fileExists(atPath: f), "closure is fake, file stays")
        XCTAssertFalse(fm.fileExists(atPath: p(".Trash")))
    }

    func testOtherHomeNeverCallsTrashClosure() throws {
        let f = try mkfile("Downloads/x.dmg")
        var calls = 0
        let r = run(f, realHome: "/some/other/real/home", trash: { _ in calls += 1 })
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertEqual(calls, 0)
    }

    func testRefusedNeverCallsTrashClosure() throws {
        var calls = 0
        let r = run("/etc/hosts", realHome: home, trash: { _ in calls += 1 })
        assertRefused(r)
        XCTAssertEqual(calls, 0)
    }

    func testTrashClosureFailureIsExit4() throws {
        struct Boom: Error, LocalizedError { var errorDescription: String? { "boom" } }
        let f = try mkfile("Downloads/x.dmg")
        let r = run(f, realHome: home, trash: { _ in throw Boom() })
        XCTAssertEqual(r.code, 4)
        XCTAssertTrue(r.message.contains("boom"), r.message)
    }

    // MARK: fixture Trash

    func testNameClashInFixtureTrash() throws {
        try mkfile(".Trash/x.dmg", "old")
        let f = try mkfile("Downloads/x.dmg", "new")
        let r = run(f)
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertEqual(try String(contentsOfFile: p(".Trash/x.dmg")), "old")
        XCTAssertEqual(try String(contentsOfFile: p(".Trash/x 2.dmg")), "new")
        // and a third
        let g = try mkfile("Downloads/x.dmg", "newer")
        XCTAssertEqual(run(g).code, 0)
        XCTAssertEqual(try String(contentsOfFile: p(".Trash/x 3.dmg")), "newer")
    }

    func testFolderClashNaming() throws {
        try mkdir(".Trash/App.xcarchive")
        try mkdir("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")
        XCTAssertEqual(run(p("Library/Developer/Xcode/Archives/2026-01-01/App.xcarchive")).code, 0)
        XCTAssertTrue(fm.fileExists(atPath: p(".Trash/App 2.xcarchive")))
    }

    func testTrashDirCreatedWith0700() throws {
        let f = try mkfile("Downloads/x.dmg")
        XCTAssertEqual(run(f).code, 0)
        var st = stat()
        XCTAssertEqual(lstat(p(".Trash"), &st), 0)
        XCTAssertEqual(st.st_mode & 0o777, 0o700)
    }

    func testSymlinkedTrashRefused() throws {
        let f = try mkfile("Downloads/x.dmg")
        try mkdir("elsewhere")
        try fm.createSymbolicLink(atPath: p(".Trash"), withDestinationPath: p("elsewhere"))
        assertRefused(run(f))
        XCTAssertTrue(fm.fileExists(atPath: f))
        XCTAssertFalse(fm.fileExists(atPath: p("elsewhere/x.dmg")))
    }

    // MARK: byte-wise comparison (grapheme-cluster regressions)

    func testPrependMarkBeforeSlashDotDotRefused() throws {
        try mkdir("outside/sub")
        let out = try mkfile("outside/x")
        try mkdir("Downloads")
        try fm.createSymbolicLink(atPath: p("Downloads/a\u{0600}"), withDestinationPath: p("outside/sub"))
        let r = run(home + "/Downloads/a\u{0600}/../x")
        assertRefused(r)
        XCTAssertTrue(fm.fileExists(atPath: out))
        XCTAssertFalse(fm.fileExists(atPath: p(".Trash/x")))
    }

    func testCombiningMarkAfterSlashSymlinkDeepRefused() throws {
        try mkdir("outside/\u{0301}deep")
        let out = try mkfile("outside/\u{0301}deep/y")
        try mkdir("Downloads")
        try fm.createSymbolicLink(atPath: p("Downloads/b"), withDestinationPath: p("outside"))
        let r = run(home + "/Downloads/b/\u{0301}deep/y")
        assertRefused(r)
        XCTAssertTrue(fm.fileExists(atPath: out))
        XCTAssertFalse(fm.fileExists(atPath: p(".Trash/y")))
    }

    func testHomeDifferingByNormalizationRefused() throws {
        let nfc = "caf\u{00E9}", nfd = "cafe\u{0301}"
        let base = home
        try mkdir("\(nfc)/Downloads")
        try mkfile("\(nfc)/Downloads/x.dmg")
        let path = base + "/" + nfc + "/Downloads/x.dmg"
        let homeNFD = base + "/" + nfd
        XCTAssertEqual(Array(path.utf8).count, Array((homeNFD + "/Downloads/x.dmg").utf8).count - 1)
        switch checkTrashable(path: path, home: homeNFD, isRealHome: false) {
        case .success: XCTFail("home with different bytes must not match")
        case .failure(let r): XCTAssertTrue(r.message.contains("inside"), r.message)
        }
        XCTAssertTrue(fm.fileExists(atPath: path))
    }

    // MARK: Applications

    func check(_ path: String, isRealHome: Bool = false, uid: uid_t = getuid()) -> Result<TrashTarget, TrashRefusal> {
        checkTrashable(path: path, home: home, isRealHome: isRealHome, uid: uid)
    }

    func assertFailure(_ r: Result<TrashTarget, TrashRefusal>, contains: String? = nil,
                       file: StaticString = #filePath, line: UInt = #line) {
        switch r {
        case .success(let t): XCTFail("expected a refusal, got \(t)", file: file, line: line)
        case .failure(let e):
            if let c = contains { XCTAssertTrue(e.message.contains(c), e.message, file: file, line: line) }
        }
    }

    func testApplicationInHomeApplications() throws {
        try mkdir("Applications/Figma (v88.1).app")
        XCTAssertEqual(try check(p("Applications/Figma (v88.1).app")).get(), .application)
    }

    func testApplicationOneFolderDeep() throws {
        try mkdir("Applications/Vendor/Tool.app")
        XCTAssertEqual(try check(p("Applications/Vendor/Tool.app")).get(), .application)
    }

    func testApplicationTwoFoldersDeepRefused() throws {
        try mkdir("Applications/a/b/Tool.app")
        assertFailure(check(p("Applications/a/b/Tool.app")), contains: "not an application bundle")
    }

    func testApplicationSymlinkRefused() throws {
        try mkdir("Applications")
        try mkdir("elsewhere/Real.app")
        try fm.createSymbolicLink(atPath: p("Applications/Link.app"), withDestinationPath: p("elsewhere/Real.app"))
        assertFailure(check(p("Applications/Link.app")), contains: "symlink")
    }

    func testApplicationInSymlinkedFolderRefused() throws {
        try mkdir("Applications")
        try mkdir("elsewhere/Real.app")
        try fm.createSymbolicLink(atPath: p("Applications/Folder"), withDestinationPath: p("elsewhere"))
        assertFailure(check(p("Applications/Folder/Real.app")), contains: "symlink")
    }

    func testApplicationNotDotAppRefused() throws {
        try mkdir("Applications/Tool")
        assertFailure(check(p("Applications/Tool")), contains: "not an application bundle")
    }

    func testApplicationFileRefused() throws {
        try mkfile("Applications/X.app")
        assertFailure(check(p("Applications/X.app")), contains: "not an application bundle")
    }

    func testApplicationOtherOwnerRefused() throws {
        try mkdir("Applications/Tool.app")
        assertFailure(check(p("Applications/Tool.app"), uid: getuid() + 1), contains: "not owned by you")
    }

    func testFixtureRootOnlyForFixtureHomes() throws {
        try mkdir(".fixture-root/Applications/A.app")
        let path = p(".fixture-root/Applications/A.app")
        XCTAssertEqual(try check(path, isRealHome: false).get(), .application)
        assertFailure(check(path, isRealHome: true))
    }

    func testSystemApplicationsOnlyForRealHome() {
        // Refused before anything under /Applications is looked at.
        assertFailure(check("/Applications/Nope.app", isRealHome: false), contains: "not inside the home folder")
    }

    // The real-home rules, on a temp home only (never the real /Applications).
    func testHomeApplicationsAllowedForRealHome() throws {
        try mkdir("Applications/X.app")
        XCTAssertEqual(try check(p("Applications/X.app"), isRealHome: true).get(), .application)
    }

    func testApplicationNameWithQuoteAndNonASCII() throws {
        // POSIX mkdir keeps these exact bytes on disk, as an installer would (FileManager
        // would store the name decomposed). The scanner passes the on-disk bytes.
        try mkdir("Applications")
        let name = "Applications/Caf\u{E9} \"Beta\".app"
        XCTAssertEqual(Darwin.mkdir(p(name), 0o755), 0)
        XCTAssertEqual(try check(p(name)).get(), .application)
        XCTAssertEqual(try check(p(name), isRealHome: true).get(), .application)
        // The same name in the other normalization is not the string on disk: refused.
        let nfd = p(name).decomposedStringWithCanonicalMapping
        XCTAssertNotEqual(Array(nfd.utf8), Array(p(name).utf8))
        assertFailure(check(nfd), contains: "resolves elsewhere")
        let r = run(p(name))
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertTrue(fm.fileExists(atPath: p(".Trash/Caf\u{E9} \"Beta\".app")))
        XCTAssertFalse(fm.fileExists(atPath: p(name)))
    }

    func testApplicationInsideAnotherApplicationRefused() throws {
        try mkdir("Applications/Foo.app/Bar.app")
        assertFailure(check(p("Applications/Foo.app/Bar.app")), contains: "inside another application")
        assertFailure(check(p("Applications/Foo.app/Bar.app"), isRealHome: true), contains: "inside another application")
        assertRefused(run(p("Applications/Foo.app/Bar.app")))
        XCTAssertTrue(fm.fileExists(atPath: p("Applications/Foo.app/Bar.app")))
    }

    func testApplicationMovesIntoFixtureTrash() throws {
        try mkdir("Applications/Tool.app")
        let r = run(p("Applications/Tool.app"))
        XCTAssertEqual(r.code, 0, r.message)
        XCTAssertTrue(fm.fileExists(atPath: p(".Trash/Tool.app")))
    }
}
