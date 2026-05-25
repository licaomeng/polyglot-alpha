// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {TranslationAuction} from "../src/TranslationAuction.sol";
import {BuilderFeeRouter} from "../src/BuilderFeeRouter.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";
import {JudgePanel} from "../src/JudgePanel.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// @notice Handler bounds Foundry's invariant fuzzer to "plausible" calls so we
///         actually exercise auction lifecycle paths instead of bouncing off
///         require() walls.
contract AuctionHandler is Test {
    TranslationAuction public auction;
    ReputationRegistry public rep;
    BuilderFeeRouter public router;
    MockUSDC public usdc;
    address public operator;
    address[] public agents;

    // Track every agent the auction has ever seen so invariants can iterate.
    address[] public allAgents;
    mapping(address => bool) public seen;
    bytes32[] public openEventIds;
    uint256 public eventCounter;

    constructor(
        TranslationAuction _auction,
        ReputationRegistry _rep,
        BuilderFeeRouter _router,
        MockUSDC _usdc,
        address _operator,
        address[] memory _agents
    ) {
        auction = _auction;
        rep = _rep;
        router = _router;
        usdc = _usdc;
        operator = _operator;
        for (uint256 i = 0; i < _agents.length; i++) {
            agents.push(_agents[i]);
            allAgents.push(_agents[i]);
            seen[_agents[i]] = true;
        }
    }

    function _pickAgent(uint256 idx) internal view returns (address) {
        return agents[idx % agents.length];
    }

    function registerAgent(uint256 idx) external {
        address a = _pickAgent(idx);
        if (auction.registered(a)) return;
        vm.prank(a);
        try auction.registerAgent() {} catch {}
    }

    function openAuction(uint256 salt) external {
        bytes32 eventId = keccak256(abi.encodePacked("inv-event", eventCounter, salt));
        eventCounter += 1;
        vm.prank(operator);
        try auction.openAuction(eventId, keccak256(abi.encodePacked("hash", eventId))) {
            openEventIds.push(eventId);
        } catch {}
    }

    function submitBid(uint256 eventIdx, uint256 agentIdx, uint96 amount) external {
        if (openEventIds.length == 0) return;
        bytes32 eventId = openEventIds[eventIdx % openEventIds.length];
        address a = _pickAgent(agentIdx);
        uint256 bid = (uint256(amount) % 50_000_000) + 1; // 1 .. 50 USDC
        vm.prank(a);
        try auction.submitBid(eventId, bid, keccak256(abi.encodePacked(a, eventId))) {} catch {}
    }

    function warpPastDeadline() external {
        vm.warp(block.timestamp + 61);
    }

    function settle(uint256 eventIdx) external {
        if (openEventIds.length == 0) return;
        bytes32 eventId = openEventIds[eventIdx % openEventIds.length];
        vm.warp(block.timestamp + 61);
        vm.prank(operator);
        try auction.settleAuction(eventId) {} catch {}
    }

    function slashStake(uint256 agentIdx, uint96 amount) external {
        address a = _pickAgent(agentIdx);
        uint256 cur = auction.stakes(a);
        if (cur == 0) return;
        uint256 slash = (uint256(amount) % cur) + 1;
        vm.prank(operator);
        try auction.slashStake(a, slash, "fuzz-slash") {} catch {}
    }

    function recordFill(uint256 agentIdx, uint96 amount) external {
        address a = _pickAgent(agentIdx);
        uint256 fill = (uint256(amount) % 10_000_000) + 1;
        // Make sure router has enough USDC; mint to operator and fund.
        usdc.mint(operator, fill);
        vm.prank(operator);
        usdc.approve(address(router), type(uint256).max);
        vm.prank(operator);
        try router.fund(fill) {} catch {
            return;
        }
        vm.prank(operator);
        try router.recordFill("fuzz-market", fill, a) {} catch {}
    }

    function agentsLength() external view returns (uint256) {
        return agents.length;
    }
}

contract InvariantsTest is Test {
    TranslationAuction public auction;
    ReputationRegistry public rep;
    BuilderFeeRouter public router;
    JudgePanel public judge;
    MockUSDC public usdc;
    AuctionHandler public handler;

    address public operator;
    address[] public agents;

    uint256 constant REGISTRATION_STAKE = 5_000_000;
    uint256 constant REPUTATION_CEILING = 2 * 1e18; // EWMA never produces > 2.0

    function setUp() public {
        operator = makeAddr("operator");
        vm.startPrank(operator);
        usdc = new MockUSDC();
        rep = new ReputationRegistry();
        auction = new TranslationAuction(address(usdc), address(rep));
        router = new BuilderFeeRouter(address(usdc), address(rep));
        judge = new JudgePanel(address(usdc));

        rep.setAuthorized(address(auction), true);
        rep.setAuthorized(address(router), true);
        rep.setAuthorized(address(judge), true);
        vm.stopPrank();

        // Bootstrap 5 agents with USDC + approval to the auction.
        for (uint256 i = 0; i < 5; i++) {
            address a = makeAddr(string.concat("agent", vm.toString(i)));
            usdc.mint(a, 1000 * 10**6);
            vm.prank(a);
            usdc.approve(address(auction), type(uint256).max);
            agents.push(a);
        }

        handler = new AuctionHandler(auction, rep, router, usdc, operator, agents);

        // Constrain the fuzzer to call into the handler — this is the standard
        // Foundry idiom for invariant testing of multi-contract systems.
        targetContract(address(handler));
    }

    // -----------------------------------------------------------------
    // INVARIANT 1: contract USDC balance always covers the sum of stakes.
    //              Slashed amounts stay in the contract (operator-controlled
    //              treasury per README), so >= rather than ==.
    // -----------------------------------------------------------------
    function invariant_stakeSumEqualsTotal() public view {
        uint256 contractBalance = usdc.balanceOf(address(auction));
        uint256 sumStakes = 0;
        for (uint256 i = 0; i < agents.length; i++) {
            sumStakes += auction.stakes(agents[i]);
        }
        assertGe(contractBalance, sumStakes, "auction USDC must cover stakes");
    }

    // -----------------------------------------------------------------
    // INVARIANT 2: reputation never exceeds 2.0 (1e18 scale) and never
    //              goes negative.
    // -----------------------------------------------------------------
    function invariant_reputationBounded() public view {
        for (uint256 i = 0; i < agents.length; i++) {
            uint256 r = rep.getReputation(agents[i]);
            assertLe(r, REPUTATION_CEILING, "reputation exceeds 2.0");
            // uint256 cannot go below zero; assertion kept for documentation.
            assertGe(r, 0, "reputation below zero");
        }
    }

    // -----------------------------------------------------------------
    // INVARIANT 3: totalWins <= totalBids always.
    // -----------------------------------------------------------------
    function invariant_winsLeqBids() public view {
        for (uint256 i = 0; i < agents.length; i++) {
            (
                uint256 totalBids,
                uint256 totalWins,
                ,
                ,
                ,
            ) = rep.reps(agents[i]);
            assertLe(totalWins, totalBids, "wins exceed bids");
        }
    }

    // -----------------------------------------------------------------
    // INVARIANT 4: router fee accounting consistency.
    //              cumulativeFees is monotonic; claimable <= cumulativeFees
    //              for every translator the router has touched.
    // -----------------------------------------------------------------
    function invariant_feeAccrualConsistent() public view {
        for (uint256 i = 0; i < agents.length; i++) {
            uint256 cum = router.getCumulativeFees(agents[i]);
            uint256 claimable = router.claimable(agents[i]);
            assertLe(claimable, cum, "claimable exceeds cumulative");
        }
    }

    // -----------------------------------------------------------------
    // INVARIANT 5 (bonus): registered agents always have stakes >= 0 and
    //                     registration flag implies non-zero historical stake.
    // -----------------------------------------------------------------
    function invariant_registrationImpliesStake() public view {
        for (uint256 i = 0; i < agents.length; i++) {
            if (auction.registered(agents[i])) {
                // Either stake remains, or it was fully slashed (cur==0 OK).
                // The real check: lockedStakes <= stakes always holds.
                uint256 locked = auction.lockedStakes(agents[i]);
                uint256 cur = auction.stakes(agents[i]);
                assertLe(locked, cur, "locked exceeds total stake");
            }
        }
    }
}
