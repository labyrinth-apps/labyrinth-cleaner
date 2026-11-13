import Foundation
import Darwin
import LabyrinthKit

func resolvedRealHome() -> String {
    if let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir {
        return realpathString(String(cString: dir)) ?? String(cString: dir)
    }
    return ""
}

let result = runTrashHelper(
    arguments: Array(CommandLine.arguments.dropFirst()),
    realHome: resolvedRealHome(),
    trash: { url in try FileManager.default.trashItem(at: url, resultingItemURL: nil) })
if !result.message.isEmpty {
    FileHandle.standardError.write((result.message + "\n").data(using: .utf8)!)
}
exit(result.code)
