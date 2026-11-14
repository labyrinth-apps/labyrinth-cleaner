import Foundation

/// PATH covers Finder-launched apps (which get a bare PATH) so devclean finds docker and friends.
/// Drops every PYTHON* key so the user's Python setup can't leak into the embedded interpreter.
public func serverEnvironment(base: [String: String], home: String, helperPath: String) -> [String: String] {
    var env = base.filter { !$0.key.hasPrefix("PYTHON") }
    env["PATH"] = [
        "/opt/homebrew/bin", "/usr/local/bin", home + "/.docker/bin", home + "/.orbstack/bin",
        "/Applications/Docker.app/Contents/Resources/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ].joined(separator: ":")
    env["HOME"] = home
    env["LABYRINTH_TRASH_HELPER"] = helperPath
    return env
}

public struct FixtureOptions: Equatable {
    public var home: String
    public var simctl: String?
    public var fakeDocker: String?
    public init(home: String, simctl: String? = nil, fakeDocker: String? = nil) {
        self.home = home; self.simctl = simctl; self.fakeDocker = fakeDocker
    }
}

public struct ServerLaunch {
    public var python: String
    public var script: String
    public var arguments: [String]
    public init(python: String, script: String, arguments: [String]) {
        self.python = python; self.script = script; self.arguments = arguments
    }
}

/// [python, "-E", "-s", "-B", "-X", "utf8", script, "ui", "--app"] + fixture args.
/// -B: never write bytecode into the bundle (it would break the code signature).
public func serverLaunch(resources: URL, fixture: FixtureOptions?) -> ServerLaunch {
    let python = resources.appendingPathComponent("python/bin/python3").path
    let script = resources.appendingPathComponent("core/devclean").path
    var args = ["-E", "-s", "-B", "-X", "utf8", script, "ui", "--app"]
    if let f = fixture {
        args += ["--home", f.home]
        if let s = f.simctl { args += ["--simctl-fixture", s] }
        if let d = f.fakeDocker { args += ["--fake-docker", d] }
    }
    return ServerLaunch(python: python, script: script, arguments: args)
}

/// nil without --fixture-home; --simctl-fixture / --fake-docker without --fixture-home -> nil; values must be absolute.
public func parseFixtureOptions(_ args: [String]) -> FixtureOptions? {
    func value(_ flag: String) -> (present: Bool, value: String?) {
        guard let i = args.firstIndex(of: flag) else { return (false, nil) }
        guard i + 1 < args.count, args[i + 1].hasPrefix("/") else { return (true, nil) }
        return (true, args[i + 1])
    }
    let home = value("--fixture-home"), simctl = value("--simctl-fixture"), docker = value("--fake-docker")
    guard let h = home.value else { return nil }
    if simctl.present && simctl.value == nil { return nil }
    if docker.present && docker.value == nil { return nil }
    return FixtureOptions(home: h, simctl: simctl.value, fakeDocker: docker.value)
}
