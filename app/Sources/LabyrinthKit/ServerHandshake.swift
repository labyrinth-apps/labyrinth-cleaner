import Foundation

public struct ServerHandshake: Equatable {
    public let url: URL
    public let port: Int
}

/// nil unless: JSON object, labyrinth_server == 1, url scheme http, host 127.0.0.1, port set, non-empty `t` query.
public func parseHandshake(_ line: String) -> ServerHandshake? {
    struct Raw: Decodable { let labyrinth_server: Int; let url: String }
    guard let data = line.data(using: .utf8),
          let raw = try? JSONDecoder().decode(Raw.self, from: data),
          raw.labyrinth_server == 1,
          let comps = URLComponents(string: raw.url),
          comps.scheme == "http", comps.host == "127.0.0.1",
          let port = comps.port,
          let token = comps.queryItems?.first(where: { $0.name == "t" })?.value, !token.isEmpty,
          let url = comps.url else { return nil }
    return ServerHandshake(url: url, port: port)
}
