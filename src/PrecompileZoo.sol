// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Precompile zoo — a dApp that touches precompiles beyond 0x0820.
///
/// @dev Why this exists: the persistent-agent consumer proves one precompile
///      works. This contract proves the *mock framework* generalises — the same
///      manifest mechanism stands in for the synchronous, short-running async,
///      and long-running async execution models alike, so any Ritual dApp can
///      be built and tested locally, not just agent dApps.
///
///      Three execution models, three different shapes:
///
///        sync          JQ 0x0803          result returned inline
///        short async   HTTP 0x0801, LLM 0x0802
///                                        returns abi.encode(simmedInput, actualOutput)
///        long async    LR-HTTP 0x0805, IMAGE 0x0818
///                                        Phase 1 returns a task id, Phase 2
///                                        calls back through AsyncDelivery
///
///      Requests for the async precompiles are prebuilt bytes, matching how the
///      official persistent-agent consumer works (it forwards a request the
///      encoder produced). Sync precompiles are encoded inline — three fields.
contract PrecompileZoo {
    address constant JQ = 0x0000000000000000000000000000000000000803;
    address constant HTTP_CALL = 0x0000000000000000000000000000000000000801;
    address constant LLM = 0x0000000000000000000000000000000000000802;
    address constant LONG_HTTP = 0x0000000000000000000000000000000000000805;
    address constant IMAGE_CALL = 0x0000000000000000000000000000000000000818;

    address constant ASYNC_DELIVERY = 0x5A16214fF555848411544b005f7Ac063742f39F6;

    /// StorageRef (platform, path, keyRef) — the LLM result carries an updated
    /// conversation history in this shape.
    struct StorageRef {
        string platform;
        string path;
        string keyRef;
    }

    /// Phase-2 results, keyed by the job id AsyncDelivery used.
    mapping(bytes32 => bytes) public resultOf;

    event JqRead(uint256 value);
    event HttpFetched(uint16 status, bytes body, string error);
    event LlmAnswered(bytes completion, string error);
    event JobStarted(bytes4 indexed kind, string taskId);
    event JobResult(bytes4 indexed kind, bytes32 indexed jobId, bytes result);

    error NotAsyncDelivery();
    error PrecompileCallFailed(address target);

    // ── sync ─────────────────────────────────────────────────────────────────

    /// @dev JQ outputType 1 = uint256, so the result decodes directly with no
    ///      string double-indirection.
    function readJq(string calldata query, string calldata json)
        external
        returns (uint256 value)
    {
        (bool ok, bytes memory raw) = JQ.call(abi.encode(query, json, uint8(1)));
        if (!ok) revert PrecompileCallFailed(JQ);
        value = abi.decode(raw, (uint256));
        emit JqRead(value);
    }

    // ── short-running async ──────────────────────────────────────────────────

    /// @dev Short-running precompiles wrap the answer:
    ///      abi.encode(bytes simmedInput, bytes actualOutput).
    function fetchHttp(bytes calldata request)
        external
        returns (uint16 status, bytes memory body, string memory err)
    {
        (bool ok, bytes memory raw) = HTTP_CALL.call(request);
        if (!ok) revert PrecompileCallFailed(HTTP_CALL);
        bytes memory out = _unwrap(raw);
        (status, , , body, err) = abi.decode(out, (uint16, string[], string[], bytes, string));
        emit HttpFetched(status, body, err);
    }

    function askLlm(bytes calldata request)
        external
        returns (bytes memory completion, string memory err)
    {
        (bool ok, bytes memory raw) = LLM.call(request);
        if (!ok) revert PrecompileCallFailed(LLM);
        bytes memory out = _unwrap(raw);
        bool hasError;
        StorageRef memory history;
        (hasError, completion, , err, history) =
            abi.decode(out, (bool, bytes, bytes, string, StorageRef));
        history; // retained for shape fidelity with the real result layout
        if (!hasError) err = "";
        emit LlmAnswered(completion, err);
    }

    /// @dev In eth_call simulation the live chain leaves actualOutput empty;
    ///      fall back to the raw bytes so the mock's answer still surfaces.
    function _unwrap(bytes memory raw) internal pure returns (bytes memory) {
        if (raw.length < 64) return raw;
        (bytes memory simmed, bytes memory actual) = abi.decode(raw, (bytes, bytes));
        return actual.length > 0 ? actual : simmed;
    }

    // ── long-running async ───────────────────────────────────────────────────

    function startLongHttp(bytes calldata request) external returns (string memory taskId) {
        (bool ok, bytes memory raw) = LONG_HTTP.call(request);
        if (!ok) revert PrecompileCallFailed(LONG_HTTP);
        taskId = abi.decode(raw, (string));
        emit JobStarted(_kind("lrh"), taskId);
    }

    function startImage(bytes calldata request) external returns (string memory taskId) {
        (bool ok, bytes memory raw) = IMAGE_CALL.call(request);
        if (!ok) revert PrecompileCallFailed(IMAGE_CALL);
        taskId = abi.decode(raw, (string));
        emit JobStarted(_kind("img"), taskId);
    }

    /// Phase-2 callback for the long-running HTTP job.
    function onLongResult(bytes32 jobId, bytes calldata result) external {
        if (msg.sender != ASYNC_DELIVERY) revert NotAsyncDelivery();
        resultOf[jobId] = result;
        emit JobResult(_kind("lrh"), jobId, result);
    }

    /// Phase-2 callback for the image job — a *different* selector, which is the
    /// point: the mock reads the declared selector from the request rather than
    /// assuming the persistent-agent callback.
    function onImageResult(bytes32 jobId, bytes calldata result) external {
        if (msg.sender != ASYNC_DELIVERY) revert NotAsyncDelivery();
        resultOf[jobId] = result;
        emit JobResult(_kind("img"), jobId, result);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    function _kind(string memory k) internal pure returns (bytes4) {
        return bytes4(keccak256(bytes(k)));
    }
}
