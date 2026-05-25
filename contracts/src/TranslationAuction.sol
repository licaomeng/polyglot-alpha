// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Minimal ERC20 interface; only the calls TranslationAuction makes.
interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address owner) external view returns (uint256);
}

interface IReputationRegistry {
    function getReputation(address agent) external view returns (uint256);
    function updateOnAuction(address agent, bool won) external;
}

/// @title TranslationAuction
/// @notice Sealed-bid (open-book in practice for hackathon) auction where AI
///         translation agents bid USDC for the right to translate a Chinese
///         news event into a Polymarket-shaped market question. Each auction
///         has a fixed 60-second submission window. The winner is chosen by
///         the largest reputation-adjusted bid: bid / max(reputation, 1.0).
contract TranslationAuction is ReentrancyGuard {
    // ---------------------------------------------------------------
    // Constants
    // ---------------------------------------------------------------

    /// @notice Mandatory stake required to register as a bidding agent (5 USDC).
    ///         USDC has 6 decimals on most chains but Arc testnet's bridged
    ///         USDC is 18-decimal; we keep this as 5 * 1e6 and let the deploy
    ///         script swap based on the live token's decimals.
    uint256 public constant REGISTRATION_STAKE = 5_000_000; // 5 USDC @ 6 decimals

    /// @notice Auction submission window length in seconds.
    uint256 public constant AUCTION_WINDOW_SECONDS = 60;

    /// @notice Fixed-point ONE used to read ReputationRegistry scores (1e18).
    uint256 public constant REPUTATION_ONE = 1e18;

    /// @notice Minimum reputation an agent must hold to submit a bid (README §5.6:
    ///         reputation gate at 0.7). Agents below this threshold are rejected
    ///         to keep low-quality bidders out of the auction.
    uint256 public constant MIN_REPUTATION_TO_BID = 7e17;

    /// @notice Length of the post-settlement window during which the winner's
    ///         registration stake remains slashable (README §5.6: "5 USDC stays
    ///         locked for 72h, slashable on Polymarket review").
    uint256 public constant SLASHABLE_WINDOW_SECONDS = 72 hours;

    // ---------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------

    struct Auction {
        bytes32 eventHash;
        uint256 deadline;
        address[] bidders;
        mapping(address => uint256) bids;
        mapping(address => bytes32) candidateMetadataHashes;
        address winner;
        uint256 winningBid;
        bool settled;
        bool opened;
    }

    /// @notice operator (deployer) is allowed to open auctions, settle, slash.
    address public operator;

    /// @notice USDC token used for stakes and bids.
    IERC20 public immutable usdc;

    /// @notice Reputation registry consulted at settle-time for reputation-weighting.
    IReputationRegistry public reputation;

    /// @notice Per-agent unlocked stake balance.
    mapping(address => uint256) public stakes;

    /// @notice Stake amount currently locked because the agent is registered
    ///         (cannot be withdrawn until the agent explicitly unregisters).
    mapping(address => uint256) public lockedStakes;

    /// @notice Whether an address is currently registered as a bidder.
    mapping(address => bool) public registered;

    /// @notice eventId -> Auction. eventId is computed off-chain (typically
    ///         keccak256 of news article URL + cutoff timestamp).
    mapping(bytes32 => Auction) internal auctions;

    /// @notice eventId -> unix timestamp at which the winner's locked
    ///         registration stake becomes withdrawable. Populated at
    ///         `settleAuction` time as `block.timestamp + SLASHABLE_WINDOW_SECONDS`
    ///         (72h). Until this time, the operator may invoke `slashStake`
    ///         on the winner for malformed submissions / quality failures.
    mapping(bytes32 => uint256) public reputationStakeUnlockAt;

    /// @notice agent -> latest unlock timestamp from any auction they won.
    ///         Tracked separately so `withdrawStake` can block withdrawals
    ///         without scanning all eventIds.
    mapping(address => uint256) public stakeUnlockAt;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------

    event AgentRegistered(address indexed agent, uint256 stake);
    event StakeWithdrawn(address indexed agent, uint256 amount);
    event AuctionOpened(bytes32 indexed eventId, bytes32 eventHash, uint256 deadline);
    event BidSubmitted(
        bytes32 indexed eventId,
        address indexed bidder,
        uint256 bidAmount,
        bytes32 candidateHash
    );
    event AuctionSettled(bytes32 indexed eventId, address indexed winner, uint256 winningBid);
    event StakeSlashed(address indexed agent, uint256 amount, string reason);
    event ReputationRegistrySet(address indexed registry);
    event SlashableWindowOpened(
        bytes32 indexed eventId,
        address indexed winner,
        uint256 unlockAt
    );

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

    /// @param _usdc Arc-testnet USDC ERC20 address.
    /// @param _reputation Optional address of an already-deployed ReputationRegistry.
    ///        Pass address(0) and call setReputationRegistry later if it has not
    ///        been deployed yet.
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
    // Agent registration
    // ---------------------------------------------------------------

    /// @notice Register as a bidding agent by transferring REGISTRATION_STAKE
    ///         USDC to the contract. Caller must have approved this contract
    ///         for at least REGISTRATION_STAKE first.
    function registerAgent() external nonReentrant {
        require(!registered[msg.sender], "already registered");
        // Checks-Effects-Interactions: mark agent registered and stake-locked
        // BEFORE the external transferFrom call. A reentrant call into
        // registerAgent will hit the "already registered" guard, and
        // ReentrancyGuard provides defense in depth.
        stakes[msg.sender] += REGISTRATION_STAKE;
        lockedStakes[msg.sender] += REGISTRATION_STAKE;
        registered[msg.sender] = true;
        bool ok = usdc.transferFrom(msg.sender, address(this), REGISTRATION_STAKE);
        require(ok, "usdc transferFrom failed");
        emit AgentRegistered(msg.sender, REGISTRATION_STAKE);
    }

    /// @notice Withdraw any UN-locked stake balance to the caller. The
    ///         registration stake stays locked until the operator unregisters
    ///         the agent (via slash with zero amount, or future extension).
    ///         If the caller recently won an auction, the call reverts until
    ///         the 72h slashable window has elapsed (README §5.6).
    function withdrawStake() external nonReentrant {
        uint256 unlockAt = stakeUnlockAt[msg.sender];
        require(block.timestamp >= unlockAt, "slashable window open");
        uint256 locked = lockedStakes[msg.sender];
        uint256 total = stakes[msg.sender];
        require(total > locked, "no unlocked stake");
        uint256 amount = total - locked;
        stakes[msg.sender] = locked;
        bool ok = usdc.transfer(msg.sender, amount);
        require(ok, "usdc transfer failed");
        emit StakeWithdrawn(msg.sender, amount);
    }

    // ---------------------------------------------------------------
    // Auction lifecycle
    // ---------------------------------------------------------------

    function openAuction(bytes32 eventId, bytes32 eventHash) external onlyOperator {
        Auction storage a = auctions[eventId];
        require(!a.opened, "already opened");
        a.eventHash = eventHash;
        a.deadline = block.timestamp + AUCTION_WINDOW_SECONDS;
        a.opened = true;
        emit AuctionOpened(eventId, eventHash, a.deadline);
    }

    /// @notice Submit a bid for an open auction.
    /// @param eventId The auction id (must already be opened).
    /// @param bidAmount Bid in USDC (held as commitment only — not transferred
    ///        until settle; the auction is a sealed-bid commitment auction for
    ///        the hackathon).
    /// @param candidateHash keccak256 of the candidate translation JSON blob.
    function submitBid(bytes32 eventId, uint256 bidAmount, bytes32 candidateHash) external {
        Auction storage a = auctions[eventId];
        require(a.opened, "not opened");
        require(!a.settled, "settled");
        require(block.timestamp < a.deadline, "window closed");
        require(registered[msg.sender], "not registered");
        require(bidAmount > 0, "zero bid");

        // Reputation gate (README §5.6 final mechanism design): agents below
        // 0.7 reputation are excluded from the auction. Unknown agents resolve
        // to ONE (1.0) from ReputationRegistry.getReputation, so newcomers pass.
        if (address(reputation) != address(0)) {
            uint256 rep = reputation.getReputation(msg.sender);
            require(rep >= MIN_REPUTATION_TO_BID, "reputation gate");
        }

        // First-time bid for this auction -> push into bidders list.
        if (a.bids[msg.sender] == 0) {
            a.bidders.push(msg.sender);
        }
        a.bids[msg.sender] = bidAmount;
        a.candidateMetadataHashes[msg.sender] = candidateHash;
        emit BidSubmitted(eventId, msg.sender, bidAmount, candidateHash);
    }

    /// @notice Settle the auction. Picks the bidder with the largest score:
    ///         score = bid / max(reputation, 1.0).
    ///         Ties are broken by first-seen (lower bidder index).
    function settleAuction(bytes32 eventId) external onlyOperator {
        Auction storage a = auctions[eventId];
        require(a.opened, "not opened");
        require(!a.settled, "already settled");
        require(block.timestamp >= a.deadline, "window open");

        uint256 nBidders = a.bidders.length;
        address bestBidder = address(0);
        uint256 bestScore = 0;
        uint256 bestBid = 0;

        for (uint256 i = 0; i < nBidders; i++) {
            address bidder = a.bidders[i];
            uint256 bid = a.bids[bidder];
            uint256 rep = address(reputation) == address(0)
                ? REPUTATION_ONE
                : reputation.getReputation(bidder);
            if (rep < REPUTATION_ONE) {
                rep = REPUTATION_ONE; // floor at 1.0 per spec
            }
            // score = bid * 1e18 / rep  (keeps integer precision)
            uint256 score = (bid * REPUTATION_ONE) / rep;
            if (score > bestScore) {
                bestScore = score;
                bestBidder = bidder;
                bestBid = bid;
            }
        }

        a.winner = bestBidder;
        a.winningBid = bestBid;
        a.settled = true;

        // Push reputation updates: winner gets won=true, losers get won=false.
        if (address(reputation) != address(0)) {
            for (uint256 i = 0; i < nBidders; i++) {
                address bidder = a.bidders[i];
                reputation.updateOnAuction(bidder, bidder == bestBidder);
            }
        }

        // Open the 72-hour slashable window on the winner's stake (README §5.6).
        // Operator may call `slashStake` during this window for malformed
        // submissions; the winner's `withdrawStake` is blocked until expiry.
        if (bestBidder != address(0)) {
            uint256 unlockAt = block.timestamp + SLASHABLE_WINDOW_SECONDS;
            reputationStakeUnlockAt[eventId] = unlockAt;
            if (unlockAt > stakeUnlockAt[bestBidder]) {
                stakeUnlockAt[bestBidder] = unlockAt;
            }
            emit SlashableWindowOpened(eventId, bestBidder, unlockAt);
        }

        emit AuctionSettled(eventId, bestBidder, bestBid);
    }

    // ---------------------------------------------------------------
    // Operator: stake slashing
    // ---------------------------------------------------------------

    /// @notice Operator can slash an agent's stake (e.g. on quality failure
    ///         flagged by the 11-judge ensemble). Slashed funds remain in
    ///         the contract treasury (operator-controlled).
    function slashStake(address agent, uint256 amount, string calldata reason)
        external
        onlyOperator
    {
        uint256 current = stakes[agent];
        require(amount > 0 && amount <= current, "bad slash amount");
        stakes[agent] = current - amount;
        if (lockedStakes[agent] >= amount) {
            lockedStakes[agent] -= amount;
        } else {
            lockedStakes[agent] = 0;
        }
        emit StakeSlashed(agent, amount, reason);
    }

    // ---------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------

    function getAuction(bytes32 eventId)
        external
        view
        returns (
            bytes32 eventHash,
            uint256 deadline,
            address winner,
            uint256 winningBid,
            bool settled,
            bool opened,
            uint256 bidderCount
        )
    {
        Auction storage a = auctions[eventId];
        return (
            a.eventHash,
            a.deadline,
            a.winner,
            a.winningBid,
            a.settled,
            a.opened,
            a.bidders.length
        );
    }

    function getBid(bytes32 eventId, address bidder)
        external
        view
        returns (uint256 bidAmount, bytes32 candidateHash)
    {
        Auction storage a = auctions[eventId];
        return (a.bids[bidder], a.candidateMetadataHashes[bidder]);
    }

    function getBidder(bytes32 eventId, uint256 index) external view returns (address) {
        return auctions[eventId].bidders[index];
    }
}
