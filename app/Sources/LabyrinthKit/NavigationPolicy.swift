import Foundation

public enum NavDecision: Equatable { case allow, openExternally, cancel }

/// allow: http + 127.0.0.1 + port == serverPort. openExternally: https or mailto. cancel: everything else.
public func navigationDecision(url: URL, serverPort: Int) -> NavDecision {
    switch url.scheme?.lowercased() {
    case "http":
        return url.host == "127.0.0.1" && url.port == serverPort ? .allow : .cancel
    case "https", "mailto":
        return .openExternally
    default:
        return .cancel
    }
}
