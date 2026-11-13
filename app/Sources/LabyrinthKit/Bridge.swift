import Foundation

public enum BridgeMessage: Equatable { case pickProjectFolder }

/// Accepts only body == ["type": "pickProjectFolder"] from origin http://127.0.0.1:<serverPort>.
public func parseBridgeMessage(body: Any, originHost: String, originPort: Int, serverPort: Int) -> BridgeMessage? {
    guard originHost == "127.0.0.1", originPort == serverPort,
          let dict = body as? [String: Any], dict.count == 1,
          let type = dict["type"] as? String, type == "pickProjectFolder" else { return nil }
    return .pickProjectFolder
}

/// "window.labyrinthAddedFolder(<JSON string literal>);" with U+2028/U+2029 escaped and "</" written "<\/".
public func addedFolderScript(path: String) -> String {
    let data = (try? JSONSerialization.data(withJSONObject: path, options: .fragmentsAllowed)) ?? Data("\"\"".utf8)
    var literal = String(decoding: data, as: UTF8.self)
    literal = literal
        .replacingOccurrences(of: "\u{2028}", with: "\\u2028")
        .replacingOccurrences(of: "\u{2029}", with: "\\u2029")
        .replacingOccurrences(of: "</", with: "<\\/")
    return "window.labyrinthAddedFolder(\(literal));"
}
