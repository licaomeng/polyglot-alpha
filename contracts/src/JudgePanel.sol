// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Minimal ERC20 surface used by JudgePanel.
interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address owner) external view returns (uint256);
}

/// @title JudgePanel
/// @notice On-chain registry + attestation store for the 11-judge ensemble
///         described in README §5.6 / §5.22 (3 translation MQM judges + 8
///         style-alignment judges). Each judge stakes USDC; bias / collusion
///         is punished by `slashJudge`. Verdicts are stamped to chain via
///         `recordAttestation` so any party can audit "who judged what."
/// @dev    Two judge classes:
///           - Translation judge: 2 USDC stake (BLEU/COMET/MQM scoring).
///           - Style-alignment judge: 1 USDC stake (D1-D8 dimensions).
///         Stake constants assume the live USDC token uses 6 decimals
///         (Arc-testnet MockUSDC). If the deploy target uses 18-dec USDC,
///         redeploy with adjusted constants.
contract JudgePanel is ReentrancyGuard {
    // ---------------------------------------------------------------
    // Constants (per README §5.6 / §5.22)
    // ---------------------------------------------------------------

    /// @notice Translation-judge USDC stake (2 USDC at 6-decimal precision).
    uint256 public constant TRANSLATION_JUDGE_STAKE = 2_000_000;

    /// @notice Style-alignment-judge USDC stake (1 USDC at 6-decimal precision).
    uint256 public constant STYLE_JUDGE_STAKE = 1_000_000;

    string public constant JUDGE_TYPE_TRANSLATION = "translation";
    string public constant JUDGE_TYPE_STYLE = "style";

    // ---------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------

    address public operator;
    IERC20 public immutable usdc;

    /// @notice Currently held USDC stake per judge address.
    mapping(address => uint256) public judgeStakes;
    mapping(address => bool) public isTranslationJudge;
    mapping(address => bool) public isStyleJudge;

    /// @notice Number of attestations recorded for a judge (for collusion
    ///         analysis off-chain — the on-chain stake-slash decision still
    ///         requires an operator call).
    mapping(address => uint256) public attestationCount;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------

    event JudgeRegistered(address indexed judge, string judgeType, uint256 stake);
    event AttestationRecorded(
        bytes32 indexed eventId,
        address indexed judge,
        uint256 score,
        bytes32 attestationHash
    );
    event JudgeSlashed(address indexed judge, uint256 amount, string reason);
    event JudgeWithdrew(address indexed judge, uint256 amount);

    // ---------------------------------------------------------------
    // Modifiers
    // ---------------------------------------------------------------

    modifier onlyOperator() {
        require(msg.sender == operator, "not operator");
        _;
    }

    // ---------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------

    constructor(address _usdc) {
        require(_usdc != address(0), "usdc zero");
        operator = msg.sender;
        usdc = IERC20(_usdc);
    }

    function transferOperator(address newOperator) external onlyOperator {
        require(newOperator != address(0), "zero op");
        operator = newOperator;
    }

    // ---------------------------------------------------------------
    // Judge registration
    // ---------------------------------------------------------------

    /// @notice Stake 2 USDC and join the translation-MQM sub-panel. Caller
    ///         must have approved this contract for the stake amount first.
    function registerTranslationJudge() external nonReentrant {
        require(!isTranslationJudge[msg.sender], "already translation judge");
        // Checks-Effects-Interactions: flip the role + stake bookkeeping BEFORE
        // the external transferFrom call so any reentrant path observes the
        // up-to-date storage. ReentrancyGuard provides defense in depth.
        judgeStakes[msg.sender] += TRANSLATION_JUDGE_STAKE;
        isTranslationJudge[msg.sender] = true;
        bool ok = usdc.transferFrom(msg.sender, address(this), TRANSLATION_JUDGE_STAKE);
        require(ok, "usdc transferFrom failed");
        emit JudgeRegistered(msg.sender, JUDGE_TYPE_TRANSLATION, TRANSLATION_JUDGE_STAKE);
    }

    /// @notice Stake 1 USDC and join the style-alignment sub-panel. Caller
    ///         must have approved this contract for the stake amount first.
    function registerStyleJudge() external nonReentrant {
        require(!isStyleJudge[msg.sender], "already style judge");
        // Checks-Effects-Interactions: see registerTranslationJudge above.
        judgeStakes[msg.sender] += STYLE_JUDGE_STAKE;
        isStyleJudge[msg.sender] = true;
        bool ok = usdc.transferFrom(msg.sender, address(this), STYLE_JUDGE_STAKE);
        require(ok, "usdc transferFrom failed");
        emit JudgeRegistered(msg.sender, JUDGE_TYPE_STYLE, STYLE_JUDGE_STAKE);
    }

    // ---------------------------------------------------------------
    // Attestations (operator-pushed; off-chain orchestrator computes the
    // attestation hash + score and stamps it here for audit)
    // ---------------------------------------------------------------

    /// @notice Record an attestation produced by a judge for a specific event.
    /// @param eventId           The auction / event identifier.
    /// @param judge             Judge wallet (must be registered).
    /// @param score             Score in the judge's natural units (e.g. MQM 0-100).
    /// @param attestationHash   keccak256 of the off-chain JSON attestation.
    function recordAttestation(
        bytes32 eventId,
        address judge,
        uint256 score,
        bytes32 attestationHash
    ) external onlyOperator {
        require(
            isTranslationJudge[judge] || isStyleJudge[judge],
            "not a registered judge"
        );
        attestationCount[judge] += 1;
        emit AttestationRecorded(eventId, judge, score, attestationHash);
    }

    // ---------------------------------------------------------------
    // Slashing
    // ---------------------------------------------------------------

    /// @notice Operator slashes a judge's stake for systemic bias or collusion.
    ///         Slashed USDC stays in the contract treasury (operator-controlled).
    function slashJudge(address judge, uint256 amount, string calldata reason)
        external
        onlyOperator
    {
        uint256 current = judgeStakes[judge];
        require(amount > 0 && amount <= current, "bad slash amount");
        judgeStakes[judge] = current - amount;
        emit JudgeSlashed(judge, amount, reason);
    }

    /// @notice Operator-triggered exit: refund a judge's remaining stake and
    ///         remove them from the panel. Useful for graceful retirement.
    function withdrawJudge(address judge) external onlyOperator {
        uint256 amount = judgeStakes[judge];
        require(amount > 0, "no stake");
        judgeStakes[judge] = 0;
        isTranslationJudge[judge] = false;
        isStyleJudge[judge] = false;
        bool ok = usdc.transfer(judge, amount);
        require(ok, "usdc transfer failed");
        emit JudgeWithdrew(judge, amount);
    }

    // ---------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------

    function getJudgeInfo(address judge)
        external
        view
        returns (
            uint256 stake,
            bool translation,
            bool style,
            uint256 attestations
        )
    {
        return (
            judgeStakes[judge],
            isTranslationJudge[judge],
            isStyleJudge[judge],
            attestationCount[judge]
        );
    }
}
