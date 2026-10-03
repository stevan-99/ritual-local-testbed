// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

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

contract MockRitualWallet {
    function balanceOf(address) external pure returns (uint256) { return 10 ** 30; }
    function lockUntil(address) external pure returns (uint256) { return type(uint256).max; }
    function deposit(uint256) external payable {}
    function depositFor(address, uint256) external payable {}
    function withdraw(uint256) external {}
}

contract MockAsyncJobTracker {
    function hasPendingJobForSender(address) external pure returns (bool) { return false; }
    function senderPreEnabled(address) external pure returns (bool) { return true; }
}

contract MockAsyncDelivery {
    function deliver(address consumer, bytes32 jobId, bytes calldata result) external {
        IConsumer(consumer).onPersistentAgentResult(jobId, result);
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
        bytes memory ret = abi.encode(derive(owner, keyIndex), M.PUBKEY);
        assembly { return(add(ret, 32), mload(ret)) }
    }
}

contract MockPersistentAgent {
    address constant ASYNC_DELIVERY = 0x5A16214fF555848411544b005f7Ac063742f39F6;
    address constant VM = 0x7109709ECfa91a80626fF3989D68f67F5b1DD12D;

    function _jobId() internal view returns (bytes32) {
        bytes memory p = abi.encodeWithSelector(bytes4(keccak256("getTxHash()")));
        (bool ok, bytes memory r) = VM.staticcall(p);
        if (ok && r.length == 32) {
            return abi.decode(r, (bytes32));
        }
        return M.JOB_ID;
    }

    // PERSISTENT_REQUEST = 26 static head words; delivery_target = word 6 (bytes 192:224).
    fallback (bytes calldata input) external returns (bytes memory) {
        require(input.length >= 224, "input too short");
        bytes32 w6 = bytes32(input[192:224]);
        address delivery_target = address(uint160(uint256(w6)));

        bytes memory result = abi.encode(
            "mock-instance-0001",
            "http://127.0.0.1:8642/gateway",
            "mock-container-7f3a",
            "bafybeigdyrmockcheckpoint0000000000000000000000000000",
            "",
            "mock-gateway-token"
        );

        IDelivery(ASYNC_DELIVERY).deliver(delivery_target, _jobId(), result);
        return abi.encode(_jobId());
    }
}
