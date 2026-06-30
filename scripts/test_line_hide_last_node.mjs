/**
 * Smoke test for hideLastNode logic extracted from line.tcx
 */

function buildPathSegments(allNodes, hideLastNode, circleSize = 56, nodeGap = 12, cornerRadius = 18, horizontalPathOffset = 0) {
    const radius = circleSize / 2
    const gap = nodeGap
    const corner = cornerRadius
    const segments = []

    const n0 = allNodes[0]
    const n1 = allNodes[1]
    let seg0 = ""
    const startX = n0.x
    const startY = n0.y + radius + gap
    seg0 += `M ${startX} ${startY}`
    const verticalEndY = n0.y + radius + gap + 40
    seg0 += ` L ${startX} ${verticalEndY}`
    const turnRightEndX = n1.x - horizontalPathOffset
    const turnRightEndY = n1.y - radius - gap - 40
    const midX = (startX + turnRightEndX) / 2
    const controlY1 = verticalEndY + corner
    const controlY2 = turnRightEndY - corner
    seg0 += ` Q ${startX} ${controlY1}, ${midX} ${(verticalEndY + turnRightEndY) / 2}`
    seg0 += ` Q ${turnRightEndX} ${controlY2}, ${turnRightEndX} ${turnRightEndY}`
    seg0 += ` L ${turnRightEndX} ${n1.y - radius - gap}`
    segments.push(seg0)

    const n2 = allNodes[2]
    let seg1 = ""
    const afterNode1Y = n1.y + radius + gap
    seg1 += `M ${n1.x} ${afterNode1Y}`
    const verticalFromNode1Y = afterNode1Y + 40
    seg1 += ` L ${n1.x} ${verticalFromNode1Y}`
    const turnLeftEndX = n2.x
    const turnLeftEndY = n2.y - radius - gap - 40
    const midX2 = (n1.x + turnLeftEndX) / 2
    seg1 += ` Q ${n1.x} ${verticalFromNode1Y + corner}, ${midX2} ${(verticalFromNode1Y + turnLeftEndY) / 2}`
    seg1 += ` Q ${turnLeftEndX} ${turnLeftEndY - corner}, ${turnLeftEndX} ${turnLeftEndY}`
    seg1 += ` L ${turnLeftEndX} ${n2.y - radius - gap}`
    segments.push(seg1)

    if (hideLastNode) return segments

    const n3 = allNodes[3]
    let seg2 = ""
    const afterNode2Y = n2.y + radius + gap
    seg2 += `M ${n2.x} ${afterNode2Y}`
    const verticalFromNode2Y = afterNode2Y + 40
    seg2 += ` L ${n2.x} ${verticalFromNode2Y}`
    const turnRightToNode3EndX = n3.x
    const turnRightToNode3EndY = n3.y - radius - gap - 40
    const midX3 = (n2.x + turnRightToNode3EndX) / 2
    seg2 += ` Q ${n2.x} ${verticalFromNode2Y + corner}, ${midX3} ${(verticalFromNode2Y + turnRightToNode3EndY) / 2}`
    seg2 += ` Q ${turnRightToNode3EndX} ${turnRightToNode3EndY - corner}, ${turnRightToNode3EndX} ${turnRightToNode3EndY}`
    seg2 += ` L ${turnRightToNode3EndX} ${n3.y - radius - gap}`
    segments.push(seg2)

    return segments
}

function getActiveNodeIndex(latest, hideLastNode, thresholds) {
    const [node1, node2, node3, node4] = thresholds
    if (hideLastNode) {
        if (latest >= node3) return 2
        if (latest >= node2) return 1
        if (latest >= node1) return 0
        return 0
    }
    if (latest >= node4) return 3
    if (latest >= node3) return 2
    if (latest >= node2) return 1
    if (latest >= node1) return 0
    return 0
}

const allNodes = [
    { x: 28, y: 28 },
    { x: 206, y: 260 },
    { x: 85, y: 803 },
    { x: 260, y: 1293 },
]

const thresholds = [0, 0.33, 0.66, 1]
let failed = 0

function assert(name, condition) {
    if (condition) {
        console.log(`✓ ${name}`)
    } else {
        console.error(`✗ ${name}`)
        failed++
    }
}

const segmentsFull = buildPathSegments(allNodes, false)
const segmentsHidden = buildPathSegments(allNodes, true)
const visibleFull = allNodes
const visibleHidden = allNodes.slice(0, 3)

assert("4 nodes by default", visibleFull.length === 4)
assert("3 nodes when hideLastNode", visibleHidden.length === 3)
assert("3 path segments by default", segmentsFull.length === 3)
assert("2 path segments when hideLastNode", segmentsHidden.length === 2)

assert(
    "max active index = 3 with 4 nodes",
    getActiveNodeIndex(1, false, thresholds) === 3
)
assert(
    "max active index = 2 when hideLastNode",
    getActiveNodeIndex(1, true, thresholds) === 2
)
assert(
    "at 0.66 scroll: node index 2 when hidden",
    getActiveNodeIndex(0.66, true, thresholds) === 2
)
assert(
    "at 0.66 scroll: node index 2 when full (not last yet)",
    getActiveNodeIndex(0.66, false, thresholds) === 2
)

// When last node hidden and scroll at max, both segments should be "completed"
const activeHidden = getActiveNodeIndex(1, true, thresholds)
const completedCount = segmentsHidden.filter((_, i) => i < activeHidden).length
assert(
    "all segments completed at end when hideLastNode",
    completedCount === segmentsHidden.length
)

if (failed === 0) {
    console.log("\nAll checks passed.")
    process.exit(0)
} else {
    console.error(`\n${failed} check(s) failed.`)
    process.exit(1)
}
