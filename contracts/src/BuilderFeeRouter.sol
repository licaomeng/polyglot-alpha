// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address owner) external view returns (uint256);
}

interface IReputationRegistry {
    function updateOnFee(address agent, uint256 amount) external;
}

/// @title BuilderFeeRouter
/// @notice Records builder-fee accrual to winning translators per Polymarket
///         fill event, lets translators claim their accumulated USDC, and
///         exposes a top-10 leaderboard view.
/// @dev    The operator (Polymarket fill listener service) is the sole address
///         allowed to invoke recordFill. recordFill assumes the corresponding
///         USDC has already been deposited into this contract (e.g. via the
///         builder-fee payout). For convenience there is also `fund()` so the
///         operator can pre-deposit USDC tokens.
contract BuilderFeeRouter is ReentrancyGuard {
    // ---------------------------------------------------------------
    // Constants
    // ---------------------------------------------------------------

    /// @notice Number of leaderboard entries returned by getLeaderboard().
    uint256 public constant LEADERBOARD_SIZE = 10;

    // ---------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------

    address public operator;
    IERC20 public immutable usdc;
    IReputationRegistry public reputation;

    /// @notice Total fees earned (lifetime) per translator, including already-claimed.
    mapping(address => uint256) public cumulativeFees;

    /// @notice Currently claimable USDC balance per translator.
    mapping(address => uint256) public claimable;

    /// @notice Number of fills recorded for a translator.
    mapping(address => uint256) public fillCount;

    /// @notice All translators ever credited (for leaderboard iteration).
    address[] public translators;
    mapping(address => bool) internal isKnownTranslator;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------

    event PayoutAccrued(
        address indexed translator,
        string marketId,
        uint256 amount,
        uint256 newCumulative
    );
    event FeesClaimed(address indexed translator, uint256 totalAmount);
    event ReputationRegistrySet(address indexed registry);
    event Funded(address indexed from, uint256 amount);

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

    constructor(address _usdc, address _reputation) {
        require(_usdc != address(0), "usdc zero");
        operator = msg.sender;
        usdc = IERC20(_usdc);
        if (_reputation != address(0)) {
            reputation = IReputationRegistry(_reputation);
        }
    }

    function setReputationRegistry(address _reputation) external onlyOperator {
        require(_reputation != address(0), "reputation zero");
        reputation = IReputationRegistry(_reputation);
        emit ReputationRegistrySet(_reputation);
    }

    function transferOperator(address newOperator) external onlyOperator {
        require(newOperator != address(0), "zero op");
        operator = newOperator;
    }

    // ---------------------------------------------------------------
    // Funding (so the contract has USDC to pay out)
    // ---------------------------------------------------------------

    /// @notice Pull USDC from caller into the router. Operator usually calls
    ///         this after withdrawing builder fees from Polymarket.
    function fund(uint256 amount) external nonReentrant {
        require(amount > 0, "zero fund");
        bool ok = usdc.transferFrom(msg.sender, address(this), amount);
        require(ok, "usdc transferFrom failed");
        emit Funded(msg.sender, amount);
    }

    // ---------------------------------------------------------------
    // Recording & claiming
    // ---------------------------------------------------------------

    /// @notice Record a Polymarket fill that credits builder fees to a translator.
    /// @param marketId Off-chain Polymarket market identifier (passed through in event).
    /// @param fillAmount USDC amount in the token's base units to credit to translator.
    /// @param translator Address of the winning translator entitled to the fee.
    function recordFill(
        string calldata marketId,
        uint256 fillAmount,
        address translator
    ) external onlyOperator {
        require(translator != address(0), "zero translator");
        require(fillAmount > 0, "zero fill");

        cumulativeFees[translator] += fillAmount;
        claimable[translator] += fillAmount;
        fillCount[translator] += 1;

        if (!isKnownTranslator[translator]) {
            isKnownTranslator[translator] = true;
            translators.push(translator);
        }

        if (address(reputation) != address(0)) {
            reputation.updateOnFee(translator, fillAmount);
        }

        emit PayoutAccrued(translator, marketId, fillAmount, cumulativeFees[translator]);
    }

    /// @notice Claim all currently-claimable USDC for `translator`. Anyone may
    ///         trigger this on behalf of the translator; funds always flow to
    ///         the translator address.
    function claimFees(address translator) external nonReentrant {
        uint256 amount = claimable[translator];
        require(amount > 0, "nothing to claim");
        claimable[translator] = 0;
        bool ok = usdc.transfer(translator, amount);
        require(ok, "usdc transfer failed");
        emit FeesClaimed(translator, amount);
    }

    // ---------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------

    function getCumulativeFees(address translator) external view returns (uint256) {
        return cumulativeFees[translator];
    }

    function getTranslatorCount() external view returns (uint256) {
        return translators.length;
    }

    /// @notice Returns the top-LEADERBOARD_SIZE translators by lifetime fees.
    ///         Performs an O(N*K) in-memory selection where N=translators.length
    ///         and K=LEADERBOARD_SIZE. Suitable for hackathon-scale leaderboards;
    ///         if N grows past ~1000 consider a paginated off-chain sort.
    function getLeaderboard()
        external
        view
        returns (address[] memory topAddrs, uint256[] memory topFees)
    {
        uint256 n = translators.length;
        uint256 k = LEADERBOARD_SIZE;
        if (k > n) {
            k = n;
        }
        topAddrs = new address[](k);
        topFees = new uint256[](k);

        // Track which entries we've already placed via "used" mask.
        bool[] memory used = new bool[](n);

        for (uint256 slot = 0; slot < k; slot++) {
            uint256 bestIdx = type(uint256).max;
            uint256 bestVal = 0;
            for (uint256 i = 0; i < n; i++) {
                if (used[i]) continue;
                uint256 v = cumulativeFees[translators[i]];
                if (bestIdx == type(uint256).max || v > bestVal) {
                    bestIdx = i;
                    bestVal = v;
                }
            }
            if (bestIdx == type(uint256).max) {
                // shouldn't happen given k <= n but defensive
                break;
            }
            used[bestIdx] = true;
            topAddrs[slot] = translators[bestIdx];
            topFees[slot] = bestVal;
        }
    }
}
