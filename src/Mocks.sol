// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Mock system contracts for the local Ritual testbed.
///
/// Three generic mock classes cover every precompile execution model, so a new
/// precompile mock is a JSON manifest entry, not a new Solidity contract:
///
///   MockSyncGeneric         sync        ONNX 0x0800, JQ 0x0803, Ed25519 0x0009,
///                                       SECP256R1 0x0100, TX_HASH 0x0830
///   MockShortAsyncGeneric   short async HTTP 0x0801, LLM 0x0802
///   MockLongRunningGeneric  long async  everything that delivers via AsyncDelivery:
///                                       0x0805, 0x0806, 0x0807, 0x080C, 0x0818,
///                                       0x0819, 0x081A, 0x0820
///
/// Every mock's response is data loaded at runtime, never hardcoded behaviour.

library M {
    bytes constant PUBKEY = hex"0290c943b27d5a7cb026bd9c70705c92d5281c2082750bb3d444328eb8a259b943";
    bytes32 constant JOB_ID = keccak256("ritual-mock-persistent-job");
}

interface IConsumer {
    function onPersistentAgentResult(bytes32 jobId, bytes calldata result) external;
}

interface IDelivery {
    function deliver(address consumer, bytes32 jobId, bytes calldata result) external;
}

/// Selector registration is separate from delivery so the delivered callback
/// selector stays a property of the request, exactly as the real protocol
/// treats the `deliverySelector` field.
interface IDeliveryRegister {
    function setSelector(address consumer, bytes32 jobId, bytes4 selector) external;
}

contract MockTEERegistry {
    struct Node {
        address paymentAddress;
        address teeAddress;
        uint8 teeType;
        bytes publicKey;
        string endpoint;
        bytes32 certPubKeyHash;
        uint8 capability;
    }
    struct Service {
        Node node;
        bool isValid;
        bytes32 workloadId;
    }

    function getServicesByCapability(uint8, bool) external pure returns (Service[] memory) {
        Service[] memory out = new Service[](1);
        out[0] = Service({
            node: Node({
                paymentAddress: 0x0000000000000000000000000000000000000002,
                teeAddress: 0x0000000000000000000000000000000000000001,
                teeType: 0,
                publicKey: M.PUBKEY,
                endpoint: "http://mock-executor",
                certPubKeyHash: bytes32(0),
                capability: 0
            }),
            isValid: true,
            workloadId: bytes32(0)
        });
        return out;
    }
}

// Stubs a payable interface (deposit/depositFor/withdraw) so the consumer's
// RitualWallet calls resolve. No ETH is ever actually held.
// forge-lint: disable-next-line(locked-ether)
contract MockRitualWallet {
    function balanceOf(address) external pure returns (uint256) { return 10 ** 30; }
    function lockUntil(address) external pure returns (uint256) { return type(uint256).max; }
    // These three exist only so the consumer's RitualWallet calls resolve; the
    // bodies are deliberately empty (a mock must neither revert nor move ETH).
    // forge-lint: disable-next-line(empty-block)
    function deposit(uint256) external payable {}
    // forge-lint: disable-next-line(empty-block)
    function depositFor(address, uint256) external payable {}
    // forge-lint: disable-next-line(empty-block)
    function withdraw(uint256) external {}
}

contract MockAsyncJobTracker {
    function hasPendingJobForSender(address) external pure returns (bool) { return false; }
    function senderPreEnabled(address) external pure returns (bool) { return true; }
}

/// @dev Phase-2 delivery. The callback selector comes from the request that
///      launched the job (see setSelector), defaulting to the persistent-agent
///      callback when a launcher did not declare one.
contract MockAsyncDelivery is IDeliveryRegister {
    mapping(address => mapping(bytes32 => bytes4)) public selectorOf;

    event SelectorSet(address indexed consumer, bytes32 indexed jobId, bytes4 selector);

    function setSelector(address consumer, bytes32 jobId, bytes4 selector) external {
        selectorOf[consumer][jobId] = selector;
        emit SelectorSet(consumer, jobId, selector);
    }

    // `consumer` is not zero-checked on purpose: an unset consumer resolves
    // to a zero selector below, which is the intended no-op path.
    // forge-lint: disable-next-line(missing-zero-check)
    function deliver(address consumer, bytes32 jobId, bytes calldata result) external {
        bytes4 sel = selectorOf[consumer][jobId];
        if (sel == bytes4(0)) {
            // default: the persistent-agent callback, so existing consumers that
            // never declared a selector keep working unchanged
            IConsumer(consumer).onPersistentAgentResult(jobId, result);
            return;
        }
        (bool ok, bytes memory ret) = consumer.call(abi.encodeWithSelector(sel, jobId, result));
        if (!ok) {
            // bubble the consumer's revert reason, matching a direct call
            assembly { revert(add(ret, 32), mload(ret)) }
        }
    }
}

contract MockDKMS {
    bytes32 constant NAME = keccak256("ritual-dkms-child");

    function derive(address owner, uint256 keyIndex) internal pure returns (address) {
        return address(uint160(uint256(keccak256(abi.encodePacked(NAME, owner, keyIndex)))));
    }

    fallback (bytes calldata input) external returns (bytes memory) {
        (
            address dkms_executor,
            bytes[] memory dkms_secrets,
            uint256 dkms_ttl,
            bytes[] memory dkms_sigs,
            bytes memory dkms_user_pk,
            address owner,
            uint256 keyIndex,
            uint8 dkms_keyFormat
        ) = abi.decode(input, (address, bytes[], uint256, bytes[], bytes, address, uint256, uint8));

        // Only owner/keyIndex shape the response. The rest are decoded so that a
        // differently-shaped request reverts here rather than silently passing;
        // they are named to document the request layout, then referenced to mark
        // that leaving them unused is deliberate.
        dkms_executor; dkms_secrets; dkms_ttl; dkms_sigs; dkms_user_pk; dkms_keyFormat;

        bytes memory ret = abi.encode(derive(owner, keyIndex), M.PUBKEY);
        assembly { return(add(ret, 32), mload(ret)) }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Generic mocks — response is data, layout is configuration
// ─────────────────────────────────────────────────────────────────────────────

/// @dev Synchronous precompiles: the response is returned verbatim.
///      Used for ONNX (0x0800), JQ (0x0803), Ed25519 (0x0009),
///      SECP256R1 (0x0100), TX_HASH (0x0830).
contract MockSyncGeneric {
    bytes public response;

    event ResponseLoaded(uint256 size);

    function setResponse(bytes calldata r) external {
        response = r;
        emit ResponseLoaded(r.length);
    }

    fallback (bytes calldata) external returns (bytes memory) {
        require(response.length > 0, "no response configured");
        return response;
    }
}

/// @dev Short-running async precompiles return
///      `abi.encode(bytes simmedInput, bytes actualOutput)`. Configure the
///      actual output; the envelope is built here.
///      Used for HTTP (0x0801) and LLM (0x0802).
contract MockShortAsyncGeneric {
    bytes public response;

    event ResponseLoaded(uint256 size);

    function setResponse(bytes calldata r) external {
        response = r;
        emit ResponseLoaded(r.length);
    }

    fallback (bytes calldata input) external returns (bytes memory) {
        require(response.length > 0, "no response configured");
        return abi.encode(input, response);
    }
}

/// @dev Long-running async precompiles: Phase 1 returns a launch handle, Phase 2
///      delivers the result through AsyncDelivery. Field offsets differ per
///      precompile, so the word indices of `deliveryTarget` and
///      `deliverySelector` are configuration, and so is the launch handle shape
///      (a bytes32 commitment vs a string task id).
///
///      Covers 0x0805, 0x0806, 0x0807, 0x080C, 0x0818, 0x0819, 0x081A, 0x0820.
contract MockLongRunningGeneric {
    address constant ASYNC_DELIVERY = 0x5A16214fF555848411544b005f7Ac063742f39F6;
    address constant VM = 0x7109709ECfa91a80626fF3989D68f67F5b1DD12D;
    bytes32 constant DEFAULT_JOB_ID = keccak256("ritual-mock-longrun-job");

    bytes public payload;
    bytes32 public configuredJobId;
    string public taskId;

    uint8 public targetWord;
    uint8 public selectorWord;
    bool public launchAsString;

    event PayloadLoaded(uint256 size);
    event JobIdSet(bytes32 jobId);
    event TaskIdSet(string taskId);
    event LayoutSet(uint8 targetWord, uint8 selectorWord, bool launchAsString);
    event Delivered(address indexed target, bytes32 indexed jobId, bytes4 selector, uint256 size);

    /// @notice Raw bytes delivered to the consumer in Phase 2.
    function setPayload(bytes calldata p) external {
        payload = p;
        emit PayloadLoaded(p.length);
    }

    function setJobId(bytes32 j) external {
        configuredJobId = j;
        emit JobIdSet(j);
    }

    function setTaskId(string calldata t) external {
        taskId = t;
        emit TaskIdSet(t);
    }

    /// @param targetWord_   head-word index holding `deliveryTarget`
    /// @param selectorWord_ head-word index holding `deliverySelector`
    /// @param asString      true => Phase 1 returns abi.encode(string taskId)
    ///                      false => Phase 1 returns abi.encode(bytes32 jobId)
    function setLayout(uint8 targetWord_, uint8 selectorWord_, bool asString) external {
        targetWord = targetWord_;
        selectorWord = selectorWord_;
        launchAsString = asString;
        emit LayoutSet(targetWord_, selectorWord_, asString);
    }

    function _word(bytes calldata input, uint256 i) internal pure returns (bytes32) {
        return bytes32(input[i * 32:(i + 1) * 32]);
    }

    function _jobId() internal view returns (bytes32) {
        // vm.getTxHash() only works under a forge cheat-code context; on live
        // RPC it fails and we fall back to the configured id.
        // forge-lint: disable-next-line(unsafe-typecast)
        bytes memory p = abi.encodeWithSelector(bytes4(keccak256("getTxHash()")));
        (bool ok, bytes memory r) = VM.staticcall(p);
        if (ok && r.length == 32) {
            return abi.decode(r, (bytes32));
        }
        return configuredJobId == bytes32(0) ? DEFAULT_JOB_ID : configuredJobId;
    }

    fallback (bytes calldata input) external returns (bytes memory) {
        require(payload.length > 0, "no payload configured");
        require(input.length >= (uint256(targetWord) + 1) * 32, "input too short for targetWord");

        address target = address(uint160(uint256(_word(input, targetWord))));
        bytes32 job = _jobId();

        if (input.length >= (uint256(selectorWord) + 1) * 32) {
            // forge-lint: disable-next-line(unsafe-typecast)
            bytes4 sel = bytes4(_word(input, selectorWord));
            if (sel != bytes4(0)) {
                IDeliveryRegister(ASYNC_DELIVERY).setSelector(target, job, sel);
            }
        }

        IDelivery(ASYNC_DELIVERY).deliver(target, job, payload);
        emit Delivered(target, job, selectorOf_(target, job), payload.length);

        if (launchAsString) {
            string memory t = bytes(taskId).length == 0 ? _defaultTaskId(job) : taskId;
            return abi.encode(t);
        }
        return abi.encode(job);
    }

    function selectorOf_(address consumer, bytes32 job) internal view returns (bytes4) {
        (bool ok, bytes memory r) = ASYNC_DELIVERY.staticcall(
            // forge-lint: disable-next-line(unsafe-typecast)
            abi.encodeWithSelector(bytes4(keccak256("selectorOf(address,bytes32)")), consumer, job)
        );
        // forge-lint: disable-next-line(unsafe-typecast)
        return ok && r.length >= 32 ? bytes4(bytes32(r)) : bytes4(0);
    }

    function _defaultTaskId(bytes32 job) internal pure returns (string memory) {
        return string(abi.encodePacked("mock-task-", _hex(job)));
    }

    function _hex(bytes32 b) internal pure returns (string memory) {
        bytes memory alphabet = "0123456789abcdef";
        bytes memory out = new bytes(8);
        for (uint256 i = 0; i < 4; i++) {
            out[i * 2] = alphabet[uint8(b[i] >> 4)];
            out[i * 2 + 1] = alphabet[uint8(b[i] & 0x0f)];
        }
        return string(out);
    }
}
