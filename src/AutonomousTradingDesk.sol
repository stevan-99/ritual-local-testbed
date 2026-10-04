// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Autonomous Trading Desk
/// @notice A Ritual persistent-agent consumer that turns an agent decision
///         into a risk-checked, on-chain trade intent.
///
/// @dev Design notes
///   * The decision arrives from the 0x0820 precompile and is delivered back
///     by AsyncDelivery (0x5A16...39F6). The callback is gated on msg.sender,
///     exactly like the official PersistentAgentConsumer.
///   * The risk gate is REAL Solidity — not mocked. Every agent decision is
///     clamped against the desk's Strategy before it may become an intent, so
///     a hallucinating or compromised agent cannot size a position beyond the
///     desk's limits. This is the part that must live on-chain.
///   * A malformed payload is contained (DecodeFailed) rather than reverting
///     the delivery, so one bad agent response cannot brick the desk.
contract AutonomousTradingDesk {
    address public constant PERSISTENT_AGENT = address(0x0820);
    address public constant ASYNC_DELIVERY = 0x5A16214fF555848411544b005f7Ac063742f39F6;

    int8 public constant SHORT = -1;
    int8 public constant HOLD = 0;
    int8 public constant LONG = 1;

    struct Strategy {
        string pair;
        uint256 maxNotionalUsd; // 6dp (USDC-style)
        uint16 maxLeverage; // 1..100
        uint16 minConfidenceBps; // 0..10000
        bool longOnly;
        bool enabled;
    }

    /// @dev The exact tuple the agent is expected to return.
    struct AgentDecision {
        string pair;
        int8 action; // -1 short, 0 hold, 1 long
        uint16 confidenceBps; // 0..10000
        uint256 notionalUsd; // 6dp
        uint16 leverage; // 1..N
        string reasoning;
    }

    struct TradeIntent {
        bytes32 jobId;
        uint256 blockNumber;
        string pair;
        int8 action; // post-risk-gate action (HOLD when rejected)
        uint16 confidenceBps;
        uint256 notionalUsd;
        uint16 leverage;
        bool accepted;
        string riskReason;
        string reasoning;
    }

    address public owner;
    Strategy public strategy;
    TradeIntent[] public intents;
    mapping(bytes32 => uint256) public intentIndex; // jobId => 1-based index
    uint256 public acceptedCount;
    uint256 public rejectedCount;

    event StrategyUpdated(
        string pair,
        uint256 maxNotionalUsd,
        uint16 maxLeverage,
        uint16 minConfidenceBps,
        bool longOnly
    );
    event PrecompileCalled(address indexed precompile, bytes input, bytes output);
    event IntentRecorded(
        bytes32 indexed jobId,
        int8 action,
        uint256 notionalUsd,
        uint16 leverage,
        bool accepted,
        string riskReason
    );
    event RiskRejected(bytes32 indexed jobId, string reason);
    event DecodeFailed(bytes32 indexed jobId, bytes result);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Configure the desk's hard risk limits. Only the owner may call.
    function setStrategy(
        string calldata pair,
        uint256 maxNotionalUsd,
        uint16 maxLeverage,
        uint16 minConfidenceBps,
        bool longOnly
    ) external onlyOwner {
        require(maxLeverage >= 1 && maxLeverage <= 100, "leverage 1..100");
        require(minConfidenceBps <= 10000, "confidence <= 10000");
        strategy = Strategy(pair, maxNotionalUsd, maxLeverage, minConfidenceBps, longOnly, true);
        emit StrategyUpdated(pair, maxNotionalUsd, maxLeverage, minConfidenceBps, longOnly);
    }

    function disable() external onlyOwner {
        strategy.enabled = false;
    }

    /// @notice Hand an encoded persistent-agent request to the 0x0820
    ///         precompile. The agent's answer comes back via
    ///         onPersistentAgentResult after Phase-2 delivery.
    function requestAnalysis(bytes calldata input) external onlyOwner returns (bytes memory) {
        require(strategy.enabled, "strategy disabled");
        (bool ok, bytes memory output) = PERSISTENT_AGENT.call(input);
        require(ok, "persistent precompile call failed");
        emit PrecompileCalled(PERSISTENT_AGENT, input, output);
        return output;
    }

    /// @notice Phase-2 callback. Only AsyncDelivery may call it.
    function onPersistentAgentResult(bytes32 jobId, bytes calldata result) external {
        require(msg.sender == ASYNC_DELIVERY, "unauthorized callback sender");

        AgentDecision memory d;
        try this.decodeDecision(result) returns (
            string memory pair,
            int8 action,
            uint16 confidenceBps,
            uint256 notionalUsd,
            uint16 leverage,
            string memory reasoning
        ) {
            d = AgentDecision(pair, action, confidenceBps, notionalUsd, leverage, reasoning);
        } catch {
            emit DecodeFailed(jobId, result);
            return;
        }

        // riskGate clamps every rejection to HOLD, so this is the action that
        // actually takes effect — deliberately not d.action. Named apart from the
        // decoded `action` above to keep the two from being confused.
        (int8 effectiveAction, bool accepted, string memory reason) = riskGate(d);

        intents.push();
        TradeIntent storage it = intents[intents.length - 1];
        it.jobId = jobId;
        it.blockNumber = block.number;
        it.pair = d.pair;
        it.action = effectiveAction;
        it.confidenceBps = d.confidenceBps;
        it.notionalUsd = d.notionalUsd;
        it.leverage = d.leverage;
        it.accepted = accepted;
        it.riskReason = reason;
        it.reasoning = d.reasoning;

        if (intentIndex[jobId] == 0) intentIndex[jobId] = intents.length;
        if (accepted) {
            acceptedCount += 1;
        } else {
            rejectedCount += 1;
            emit RiskRejected(jobId, reason);
        }
        emit IntentRecorded(jobId, effectiveAction, d.notionalUsd, d.leverage, accepted, reason);
    }

    /// @notice External so the callback can contain decode failures in a
    ///         try/catch. Also handy for off-chain verification.
    /// @dev Decodes the tuple member-by-member on purpose: the agent returns
    ///      abi.encode(string,int8,uint16,uint256,uint16,string), and decoding
    ///      that into a struct directly would expect an extra outer offset
    ///      word that a bare abi.encode of the members never writes.
    function decodeDecision(bytes calldata result)
        external
        pure
        returns (
            string memory pair,
            int8 action,
            uint16 confidenceBps,
            uint256 notionalUsd,
            uint16 leverage,
            string memory reasoning
        )
    {
        return abi.decode(result, (string, int8, uint16, uint256, uint16, string));
    }

    function intentCount() external view returns (uint256) {
        return intents.length;
    }

    function latestIntent() external view returns (TradeIntent memory) {
        require(intents.length > 0, "no intents");
        return intents[intents.length - 1];
    }

    /// @dev The whole point of the desk: clamp the agent's proposal against
    ///      the hard limits. Returns the post-gate action, whether it was
    ///      accepted, and the reason.
    function riskGate(AgentDecision memory d)
        public
        view
        returns (int8 action, bool accepted, string memory reason)
    {
        if (!strategy.enabled) return (HOLD, false, "strategy disabled");
        if (keccak256(bytes(d.pair)) != keccak256(bytes(strategy.pair))) {
            return (HOLD, false, "pair mismatch");
        }
        if (d.action != SHORT && d.action != HOLD && d.action != LONG) {
            return (HOLD, false, "invalid action");
        }
        if (d.confidenceBps > 10000) return (HOLD, false, "confidence > 100%");
        if (d.confidenceBps < strategy.minConfidenceBps) {
            return (HOLD, false, "confidence below floor");
        }
        if (strategy.longOnly && d.action == SHORT) {
            return (HOLD, false, "long-only: short rejected");
        }
        if (d.action == HOLD) return (HOLD, true, "hold");
        if (d.notionalUsd > strategy.maxNotionalUsd) {
            return (HOLD, false, "notional above cap");
        }
        if (d.leverage < 1 || d.leverage > strategy.maxLeverage) {
            return (HOLD, false, "leverage out of range");
        }
        return (d.action, true, "accepted");
    }
}
