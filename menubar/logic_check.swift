// Assertion runner for Logic.swift. Prints OK or crashes with the failing check.
//   swiftc -parse-as-library Logic.swift logic_check.swift -o check && ./check

@main
struct LogicCheck {
    static func main() {
        let GB = bytesPerGB
        precondition(level(freeBytes: 15 * GB - 1) == .red, "just under 15 GB is red")
        precondition(level(freeBytes: 15 * GB) == .amber, "exactly 15 GB is amber")
        precondition(level(freeBytes: 30 * GB - 1) == .amber, "just under 30 GB is amber")
        precondition(level(freeBytes: 30 * GB) == .normal, "exactly 30 GB is normal")
        precondition(level(freeBytes: 0) == .red)

        var s = NotifyState()
        let readings: [Int64] = [40, 14, 10, 18, 21, 14]
        var got: [Bool] = []
        for r in readings { got.append(step(&s, freeBytes: r * GB)) }
        precondition(got == [false, true, false, false, false, true], "notify sequence \(got)")

        var t = NotifyState()
        _ = step(&t, freeBytes: 14 * GB)
        precondition(!step(&t, freeBytes: 20 * GB), "exactly 20 GB does not re-arm")
        precondition(!step(&t, freeBytes: 14 * GB), "still armed off after 20 GB")

        let text = formatFree(57_100_000_000)
        precondition(text.hasSuffix("GB") && text.hasPrefix("57"), "format \(text)")
        precondition(launchFailureMessage(status: 0, signaled: false, stderr: "") == nil)
        precondition(launchFailureMessage(status: 15, signaled: true, stderr: "x") == nil,
                     "being stopped is not a failure")
        let m = launchFailureMessage(status: 1, signaled: false,
                                     stderr: "a\nb\nc\nd\ne\nf\nTraceback boom\n") ?? ""
        precondition(m.contains("Traceback boom") && m.contains("exit 1"), "message \(m)")
        precondition(!m.contains("a\nb"), "only the last lines")
        print("OK")
    }
}
