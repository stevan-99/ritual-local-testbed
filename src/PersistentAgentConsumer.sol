// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Minimal Persistent Agent Consumer
/// @notice Calls the 0x0820 precompile, receives Phase 2 spawn results, and
///         also exposes a helper for the 0x081B DKMS key precompile.
contract PersistentAgentConsumer {
    address constant PERSISTENT_AGENT = address(0x0820);
    address constant DKMS_KEY = address(0x081B);
    address constant ASYNC_DELIVERY = 0x5A16214fF555848411544b005f7Ac063742f39F6;

    bytes32 public lastJobId;
    bytes public lastResult;
    bytes public lastDkmsResult;

    event PrecompileCalled(address indexed precompile, bytes input, bytes output);
    event PersistentAgentResultDelivered(bytes32 indexed jobId, bytes result);
    event DkmsKeyResult(bytes result);

    function callPersistentAgent(bytes calldata input) external returns (bytes memory) {
        (bool ok, bytes memory output) = PERSISTENT_AGENT.call(input);
        require(ok, "persistent precompile call failed");
        emit PrecompileCalled(PERSISTENT_AGENT, input, output);
        return output;
    }

    function callDKMSKey(bytes calldata input) external returns (bytes memory) {
        (bool ok, bytes memory output) = DKMS_KEY.call(input);
        require(ok, "dkms precompile call failed");
        lastDkmsResult = output;
        emit PrecompileCalled(DKMS_KEY, input, output);
        emit DkmsKeyResult(output);
        return output;
    }

    function onPersistentAgentResult(bytes32 jobId, bytes calldata result) external {
        require(msg.sender == ASYNC_DELIVERY, "unauthorized callback sender");
        lastJobId = jobId;
        lastResult = result;
        emit PersistentAgentResultDelivered(jobId, result);
    }
}
