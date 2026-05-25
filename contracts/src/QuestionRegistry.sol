// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title QuestionRegistry
/// @notice Anchors PolyglotAlpha Polymarket-style questions on Arc testnet.
///         Stores cryptographic commitments (hashes) of the question title and
///         the source Chinese news text plus auxiliary resolution metadata.
contract QuestionRegistry {
    struct Question {
        bytes32 titleHash;
        bytes32 sourceNewsHash;
        string resolutionSource;
        uint256 cutoffTs;
        string category;
        string winningTranslator;
        address submitter;
        uint256 blockTimestamp;
    }

    mapping(uint256 => Question) public questions;
    uint256 public nextId;

    event QuestionRegistered(
        uint256 indexed id,
        bytes32 indexed titleHash,
        address indexed submitter,
        string category,
        uint256 cutoffTs
    );

    function registerQuestion(
        bytes32 _titleHash,
        bytes32 _sourceNewsHash,
        string calldata _resolutionSource,
        uint256 _cutoffTs,
        string calldata _category,
        string calldata _winningTranslator
    ) external returns (uint256 id) {
        id = nextId++;
        questions[id] = Question({
            titleHash: _titleHash,
            sourceNewsHash: _sourceNewsHash,
            resolutionSource: _resolutionSource,
            cutoffTs: _cutoffTs,
            category: _category,
            winningTranslator: _winningTranslator,
            submitter: msg.sender,
            blockTimestamp: block.timestamp
        });
        emit QuestionRegistered(id, _titleHash, msg.sender, _category, _cutoffTs);
    }

    function getQuestion(uint256 _id) external view returns (Question memory) {
        return questions[_id];
    }
}
