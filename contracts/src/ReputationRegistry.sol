// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/math/Math.sol";

/// @title ReputationRegistry
/// @notice Tracks per-agent reputation across the PolyglotAlpha translation
///         auction lifecycle. Reputation is an EMA-style score that incorporates
///         win rate, post-auction quality outcomes, and realized fill revenue.
/// @dev All scores are scaled by 1e18 (i.e. 1.0 == 1e18). Unknown agents return
///      the default neutral score (1e18) so the auction can divide bids by a
///      non-zero denominator without a special case at the call site.
contract ReputationRegistry {
    // ---------------------------------------------------------------
    // Constants (NatSpec-visible "magic numbers")
    // ---------------------------------------------------------------

    /// @notice Fixed-point ONE; score units are 1e18 == 1.0.
    uint256 public constant ONE = 1e18;

    /// @notice EMA decay applied to the previous score on each update
    ///         (85% old + 15% new). Stored scaled by 1e18: 0.85e18.
    ///         Per README §5.6 final mechanism design (α = 0.85): slow decay
    ///         so one bad event drops reputation by ~0.045.
    uint256 public constant DECAY_NUMERATOR = 85e16;

    /// @notice Weight on the freshly computed signal in the EMA (0.15e18).
    uint256 public constant SIGNAL_NUMERATOR = 15e16;

    /// @notice Lower clamp for the fill-signal multiplier (0.5e18 == 0.5).
    uint256 public constant FILL_SIGNAL_MIN = 5e17;

    /// @notice Upper clamp for the fill-signal multiplier (2.0e18 == 2.0).
    uint256 public constant FILL_SIGNAL_MAX = 2e18;

    /// @notice Scale on cumulative fees (USDC, 18 decimals) used inside the
    ///         ln() of the fill-signal computation. Matches spec: ln(1 + fees/100).
    uint256 public constant FEE_SCALE = 100;

    // ---------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------

    struct Reputation {
        uint256 totalBids;
        uint256 totalWins;
        uint256 totalQualityPasses;
        uint256 cumulativeFeesEarned;
        /// @dev Score is scaled by 1e18. Default neutral is 1e18 (== 1.0).
        uint256 score;
        uint256 lastUpdated;
    }

    mapping(address => Reputation) public reps;

    /// @notice Owner deploys the contract and is the only address that can
    ///         set authorized callers (BuilderFeeRouter, TranslationAuction, operator EOA).
    address public owner;

    /// @notice Authorized callers permitted to push state updates.
    mapping(address => bool) public authorized;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------

    event AuctionUpdated(address indexed agent, bool won, uint256 newScore);
    event QualityUpdated(address indexed agent, bool passed, uint256 newScore);
    event FeeUpdated(address indexed agent, uint256 amount, uint256 newScore);
    event AuthorizedSet(address indexed who, bool allowed);
    event ReputationSlashed(
        address indexed agent,
        address indexed by,
        uint256 amount,
        uint256 newScore,
        string reason
    );

    // ---------------------------------------------------------------
    // Modifiers
    // ---------------------------------------------------------------

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier onlyAuthorized() {
        require(authorized[msg.sender] || msg.sender == owner, "not authorized");
        _;
    }

    // ---------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------

    constructor() {
        owner = msg.sender;
        authorized[msg.sender] = true;
    }

    function setAuthorized(address who, bool allowed) external onlyOwner {
        authorized[who] = allowed;
        emit AuthorizedSet(who, allowed);
    }

    // ---------------------------------------------------------------
    // Mutation: auction settled
    // ---------------------------------------------------------------

    /// @notice Called by the auction contract after every settle (for the winner
    ///         and for each losing bidder). Bumps bid/win counts then recomputes
    ///         the EMA score.
    function updateOnAuction(address agent, bool won) external onlyAuthorized {
        Reputation storage r = reps[agent];
        if (r.lastUpdated == 0) {
            r.score = ONE; // initialize unknown agent to 1.0
        }
        r.totalBids += 1;
        if (won) {
            r.totalWins += 1;
        }
        r.score = _recompute(r);
        r.lastUpdated = block.timestamp;
        emit AuctionUpdated(agent, won, r.score);
    }

    // ---------------------------------------------------------------
    // Mutation: quality verdict from 11-judge ensemble
    // ---------------------------------------------------------------

    function updateOnQuality(address agent, bool passed) external onlyAuthorized {
        Reputation storage r = reps[agent];
        if (r.lastUpdated == 0) {
            r.score = ONE;
        }
        if (passed) {
            r.totalQualityPasses += 1;
        }
        r.score = _recompute(r);
        r.lastUpdated = block.timestamp;
        emit QualityUpdated(agent, passed, r.score);
    }

    // ---------------------------------------------------------------
    // Mutation: fee accrual signal
    // ---------------------------------------------------------------

    function updateOnFee(address agent, uint256 amount) external onlyAuthorized {
        Reputation storage r = reps[agent];
        if (r.lastUpdated == 0) {
            r.score = ONE;
        }
        r.cumulativeFeesEarned += amount;
        r.score = _recompute(r);
        r.lastUpdated = block.timestamp;
        emit FeeUpdated(agent, amount, r.score);
    }

    // ---------------------------------------------------------------
    // Mutation: slashing (multi-authority — JudgePanel, TranslationAuction,
    //           BuilderFeeRouter, or operator EOA may all slash per README §5.6
    //           "the contract is the slashing authority")
    // ---------------------------------------------------------------

    /// @notice Hard-subtract reputation. Floors at zero. Any address in the
    ///         `authorized` map (set by the owner) may slash — typically that
    ///         is JudgePanel (quality verdict), TranslationAuction (malformed
    ///         submission inside the 72h window), and BuilderFeeRouter (post-
    ///         fill review). Owner can also slash directly.
    /// @param agent  Agent whose score is being reduced.
    /// @param amount Amount (in 1e18 units) to subtract from the score.
    /// @param reason Human-readable reason, emitted in the event for audit.
    function slashReputation(address agent, uint256 amount, string calldata reason)
        external
        onlyAuthorized
    {
        require(amount > 0, "zero slash");
        Reputation storage r = reps[agent];
        if (r.lastUpdated == 0) {
            r.score = ONE;
        }
        if (amount >= r.score) {
            r.score = 0;
        } else {
            r.score = r.score - amount;
        }
        r.lastUpdated = block.timestamp;
        emit ReputationSlashed(agent, msg.sender, amount, r.score, reason);
    }

    // ---------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------

    /// @notice Returns the agent's reputation score in 1e18 units.
    /// @dev Returns ONE (1e18) for any agent that has never been touched so
    ///      that the auction's `bid / reputation` divisor is safe.
    function getReputation(address agent) external view returns (uint256) {
        Reputation storage r = reps[agent];
        if (r.lastUpdated == 0) {
            return ONE;
        }
        return r.score;
    }

    function getStats(address agent)
        external
        view
        returns (
            uint256 totalBids,
            uint256 totalWins,
            uint256 totalQualityPasses,
            uint256 cumulativeFeesEarned,
            uint256 score
        )
    {
        Reputation storage r = reps[agent];
        return (
            r.totalBids,
            r.totalWins,
            r.totalQualityPasses,
            r.cumulativeFeesEarned,
            r.lastUpdated == 0 ? ONE : r.score
        );
    }

    // ---------------------------------------------------------------
    // Internal: scoring formula
    // ---------------------------------------------------------------

    /// @dev Score recomputation (README §5.6, α = 0.85):
    ///        new_score = old_score * 0.85 + (win_rate * quality_rate * fill_signal) * 0.15
    ///      where:
    ///        win_rate     = totalWins / max(totalBids, 1)
    ///        quality_rate = totalQualityPasses / max(totalWins, 1)
    ///        fill_signal  = clamp(ln(1 + cumulativeFees / 100), 0.5, 2.0)
    ///      All math is fixed-point with 1e18 scale.
    function _recompute(Reputation storage r) internal view returns (uint256) {
        uint256 winRate = r.totalBids == 0
            ? ONE
            : Math.mulDiv(r.totalWins, ONE, r.totalBids);
        uint256 qualityRate = r.totalWins == 0
            ? ONE
            : Math.mulDiv(r.totalQualityPasses, ONE, r.totalWins);
        uint256 fillSignal = _fillSignal(r.cumulativeFeesEarned);

        // signal = winRate * qualityRate * fillSignal, all 1e18-scaled.
        // mulDiv avoids precision loss and overflow on intermediate products.
        uint256 wq = Math.mulDiv(winRate, qualityRate, ONE);
        uint256 signal = Math.mulDiv(wq, fillSignal, ONE);

        // new_score = old * DECAY + signal * SIGNAL  (both 1e18-scaled weights).
        uint256 decayed = Math.mulDiv(r.score, DECAY_NUMERATOR, ONE);
        uint256 weighted = Math.mulDiv(signal, SIGNAL_NUMERATOR, ONE);
        return decayed + weighted;
    }

    /// @dev Integer-only natural-log approximation, clamped to [0.5, 2.0].
    ///      We use a 4-term Mercator series for ln(1+x) when x is small, and
    ///      saturate to FILL_SIGNAL_MAX once cumulative fees exceed the band.
    function _fillSignal(uint256 cumulativeFees) internal pure returns (uint256) {
        // x = cumulativeFees / 100 (in 1e18 units, since fees are already 1e18-scaled)
        // For Solidity-friendliness we compute a piecewise approximation.
        if (cumulativeFees == 0) {
            return FILL_SIGNAL_MIN;
        }

        // x_1e18 in fixed-point (cumulativeFees already 1e18 USDC; divide by FEE_SCALE=100)
        uint256 x = cumulativeFees / FEE_SCALE;

        // Saturate above e^2 - 1 (~6.389 in 1e18 units => 6.389e18)
        if (x >= 6_389_056_098_930_650_407) {
            return FILL_SIGNAL_MAX;
        }

        // ln(1+x) ~ x - x^2/2 + x^3/3 - x^4/4   for small x.
        // To keep things bounded for x up to ~1e18 (i.e. 1.0), we cap the series.
        if (x > ONE) {
            // For x in (1, ~6.4) use ln(1+x) = ln(2) + ln((1+x)/2) iteratively is
            // overkill on-chain. Linearly interpolate between ln(2) and ln(e^2)=2.
            // ln(2) ~ 0.6931 in 1e18 units.
            uint256 LN2 = 693_147_180_559_945_309;
            uint256 TOP = 2 * ONE;
            // x_norm = (x - 1e18) / (6.389e18 - 1e18) in 1e18 units
            uint256 num = x - ONE;
            uint256 den = 6_389_056_098_930_650_407 - ONE;
            uint256 t = Math.mulDiv(num, ONE, den);
            uint256 interp = LN2 + Math.mulDiv(TOP - LN2, t, ONE);
            return _clamp(interp, FILL_SIGNAL_MIN, FILL_SIGNAL_MAX);
        }

        // Mercator series for small x (x <= 1.0). Use mulDiv to preserve
        // precision on the chained x^n / ONE divisions.
        uint256 x2 = Math.mulDiv(x, x, ONE);
        uint256 x3 = Math.mulDiv(x2, x, ONE);
        uint256 x4 = Math.mulDiv(x3, x, ONE);
        // term magnitudes: x - x^2/2 + x^3/3 - x^4/4
        uint256 pos = x + x3 / 3;
        uint256 neg = x2 / 2 + x4 / 4;
        uint256 ln = pos > neg ? pos - neg : 0;
        return _clamp(ln, FILL_SIGNAL_MIN, FILL_SIGNAL_MAX);
    }

    function _clamp(uint256 v, uint256 lo, uint256 hi) internal pure returns (uint256) {
        if (v < lo) return lo;
        if (v > hi) return hi;
        return v;
    }
}
