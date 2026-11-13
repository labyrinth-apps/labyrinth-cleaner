import XCTest
@testable import LabyrinthKit

final class ServerEnvironmentTests: XCTestCase {
    func testEnvFinderLaunch() {
        let e = serverEnvironment(base: ["PATH": "/usr/bin:/bin", "PYTHONPATH": "/x", "PYTHONHOME": "/y", "FOO": "1"],
                                  home: "/Users/u", helperPath: "/A.app/Contents/MacOS/labyrinth-trash")
        XCTAssertTrue(e["PATH"]!.hasPrefix("/opt/homebrew/bin:/usr/local/bin:/Users/u/.docker/bin"))
        XCTAssertEqual(e["PATH"], "/opt/homebrew/bin:/usr/local/bin:/Users/u/.docker/bin:/Users/u/.orbstack/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin")
        XCTAssertNil(e["PYTHONPATH"]); XCTAssertNil(e["PYTHONHOME"])
        XCTAssertEqual(e["HOME"], "/Users/u")
        XCTAssertEqual(e["FOO"], "1")
        XCTAssertEqual(e["LABYRINTH_TRASH_HELPER"], "/A.app/Contents/MacOS/labyrinth-trash")
    }

    func testLaunchArgs() {
        let res = URL(fileURLWithPath: "/A.app/Contents/Resources")
        let a = serverLaunch(resources: res, fixture: nil)
        XCTAssertEqual(a.python, "/A.app/Contents/Resources/python/bin/python3")
        XCTAssertEqual(a.script, "/A.app/Contents/Resources/core/devclean")
        XCTAssertEqual(a.arguments, ["-E", "-s", "-X", "utf8", a.script, "ui", "--app"])
        let f = serverLaunch(resources: res, fixture: FixtureOptions(home: "/h", simctl: "/s", fakeDocker: nil))
        XCTAssertEqual(Array(f.arguments.suffix(4)), ["--home", "/h", "--simctl-fixture", "/s"])
        let g = serverLaunch(resources: res, fixture: FixtureOptions(home: "/h", simctl: nil, fakeDocker: "/d"))
        XCTAssertEqual(Array(g.arguments.suffix(4)), ["--home", "/h", "--fake-docker", "/d"])
    }

    func testFixtureOptionsParse() {
        XCTAssertNil(parseFixtureOptions([]))
        XCTAssertNil(parseFixtureOptions(["app"]))
        XCTAssertEqual(parseFixtureOptions(["app", "--fixture-home", "/t", "--simctl-fixture", "/s"]),
                       FixtureOptions(home: "/t", simctl: "/s", fakeDocker: nil))
        XCTAssertNil(parseFixtureOptions(["--fake-docker", "/d"]))
        XCTAssertNil(parseFixtureOptions(["--simctl-fixture", "/s"]))
        XCTAssertNil(parseFixtureOptions(["--fixture-home", "rel"]))
        XCTAssertNil(parseFixtureOptions(["--fixture-home", "/t", "--simctl-fixture", "rel"]))
        XCTAssertNil(parseFixtureOptions(["--fixture-home"]))
    }

    func testLegacyBarDetection() {
        XCTAssertTrue(legacyBarRunning(bundleIDs: ["com.apple.finder", "local.devclean.bar"]))
        XCTAssertFalse(legacyBarRunning(bundleIDs: ["com.apple.finder"]))
        XCTAssertFalse(legacyBarRunning(bundleIDs: []))
    }
}
