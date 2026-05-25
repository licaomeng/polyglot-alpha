// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {TranslationAuction} from "../src/TranslationAuction.sol";
import {BuilderFeeRouter} from "../src/BuilderFeeRouter.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";
import {JudgePanel} from "../src/JudgePanel.sol";
import {MockUSDC} from "./MockUSDC.sol";

contract FuzzTests is Test {
    TranslationAuction public auction;
    ReputationRegistry public rep;
    BuilderFeeRouter public router;
    MockUSDC public usdc;
    address public operator;

    uint256 constant REGISTRATION_STAKE = 5_000_000;
    uint256 constant MIN_REP_TO_BID = 7e17;
    uint256 constant ONE = 1e18;
    uint256 constant REP_CEILING = 2 * 1e18;

    function setUp() public {
        operator = makeAddr("operator");
        vm.startPrank(operator);
        usdc = new MockUSDC();
        rep = new ReputationRegistry();
        auction = new TranslationAuction(address(usdc), address(rep));
        router = new BuilderFeeRouter(address(usdc), address(rep));
        rep.setAuthorized(address(auction), true);
        rep.setAuthorized(address(router), true);
        vm.stopPrank();
    }

    // -----------------------------------------------------------------
    // Fuzz 1: registerAgent only succeeds when the caller has >= 5 USDC
    //          AND has approved the contract.
    // -----------------------------------------------------------------
    function testFuzz_RegisterAgent(uint96 amount) public {
        vm.assume(amount < 10000 * 10**6);
        address a = makeAddr("fuzzAgent");
        usdc.mint(a, amount);
        vm.prank(a);
        usdc.approve(address(auction), amount);

        if (amount >= REGISTRATION_STAKE) {
            vm.prank(a);
            auction.registerAgent();
            assertEq(auction.stakes(a), REGISTRATION_STAKE, "stake mismatch");
            assertTrue(auction.registered(a), "should be registered");
        } else {
            vm.prank(a);
            vm.expectRevert();
            auction.registerAgent();
        }
    }

    // -----------------------------------------------------------------
    // Fuzz 2: submitBid honours the reputation gate (>= 0.7e18 passes,
    //          below reverts with "reputation gate").
    // -----------------------------------------------------------------
    function testFuzz_BidWithReputation(uint256 slashAmount, uint96 bidAmount) public {
        // Bound slashAmount to [0, 1e18] so reputation lands in [0, 1.0].
        slashAmount = bound(slashAmount, 0, ONE);
        bidAmount = uint96(bound(uint256(bidAmount), 1, 10_000_000));

        address a = makeAddr("bidAgent");
        usdc.mint(a, 100_000_000);
        vm.prank(a);
        usdc.approve(address(auction), type(uint256).max);
        vm.prank(a);
        auction.registerAgent();

        bytes32 eventId = keccak256("fuzz-event");
        vm.prank(operator);
        auction.openAuction(eventId, keccak256("hash"));

        // Force reputation by slashing. Default is 1e18 (1.0).
        if (slashAmount > 0) {
            vm.prank(operator);
            rep.slashReputation(a, slashAmount, "fuzz-slash");
        }

        uint256 finalRep = rep.getReputation(a);

        if (finalRep >= MIN_REP_TO_BID) {
            vm.prank(a);
            auction.submitBid(eventId, bidAmount, keccak256("cand"));
            (uint256 stored, ) = auction.getBid(eventId, a);
            assertEq(stored, bidAmount, "bid not stored");
        } else {
            vm.prank(a);
            vm.expectRevert(bytes("reputation gate"));
            auction.submitBid(eventId, bidAmount, keccak256("cand"));
        }
    }

    // -----------------------------------------------------------------
    // Fuzz 3: EWMA stays in [0, 2.0] regardless of how many updates land.
    //          The README says α=0.85 so the score should never overflow.
    // -----------------------------------------------------------------
    function testFuzz_ReputationEwma(uint8 numUpdates, uint256 seed) public {
        vm.assume(numUpdates < 80);
        address a = makeAddr("ewmaAgent");

        for (uint256 i = 0; i < numUpdates; i++) {
            uint256 sel = uint256(keccak256(abi.encodePacked(seed, i))) % 4;
            vm.prank(operator);
            if (sel == 0) {
                rep.updateOnAuction(a, true);
            } else if (sel == 1) {
                rep.updateOnAuction(a, false);
            } else if (sel == 2) {
                rep.updateOnQuality(a, true);
            } else {
                // Bound fees to avoid silly-large numbers; recordFill caps
                // anyway via _fillSignal saturation.
                uint256 fee = (uint256(keccak256(abi.encodePacked("fee", seed, i))) % 1_000_000) + 1;
                rep.updateOnFee(a, fee);
            }
        }

        uint256 score = rep.getReputation(a);
        assertLe(score, REP_CEILING, "EWMA exceeded 2.0");
    }

    // -----------------------------------------------------------------
    // Fuzz 4: claimFees never drains more than `claimable` AND never
    //          leaves router USDC balance negative (uint underflow check).
    // -----------------------------------------------------------------
    function testFuzz_ClaimFeesConsistent(uint96 fillAmount, uint96 fundAmount) public {
        fillAmount = uint96(bound(uint256(fillAmount), 1, 10_000_000));
        fundAmount = uint96(bound(uint256(fundAmount), uint256(fillAmount), 50_000_000));

        address translator = makeAddr("translator");
        usdc.mint(operator, fundAmount);
        vm.prank(operator);
        usdc.approve(address(router), type(uint256).max);
        vm.prank(operator);
        router.fund(fundAmount);

        vm.prank(operator);
        router.recordFill("fuzz-mkt", fillAmount, translator);

        uint256 routerBalBefore = usdc.balanceOf(address(router));
        uint256 transBalBefore = usdc.balanceOf(translator);

        router.claimFees(translator);

        assertEq(usdc.balanceOf(translator) - transBalBefore, fillAmount, "claimed != fill");
        assertEq(routerBalBefore - usdc.balanceOf(address(router)), fillAmount, "router didn't decrease by fill");
        assertEq(router.claimable(translator), 0, "claimable not zeroed");
        // cumulative is monotonic
        assertEq(router.getCumulativeFees(translator), fillAmount, "cumulative changed on claim");
    }

    // -----------------------------------------------------------------
    // Fuzz 5: slashStake invariants — slash <= current stake, total
    //          decreases by exactly the slash, never reverts on legal input.
    // -----------------------------------------------------------------
    function testFuzz_SlashStake(uint96 slashAmount) public {
        address a = makeAddr("slashTarget");
        usdc.mint(a, 100_000_000);
        vm.prank(a);
        usdc.approve(address(auction), type(uint256).max);
        vm.prank(a);
        auction.registerAgent();

        uint256 before = auction.stakes(a);
        if (slashAmount == 0 || slashAmount > before) {
            vm.prank(operator);
            vm.expectRevert(bytes("bad slash amount"));
            auction.slashStake(a, slashAmount, "fuzz");
        } else {
            vm.prank(operator);
            auction.slashStake(a, slashAmount, "fuzz");
            assertEq(auction.stakes(a), before - slashAmount, "stake mismatch");
        }
    }
}
