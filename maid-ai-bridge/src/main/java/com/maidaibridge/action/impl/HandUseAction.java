package com.maidaibridge.action.impl;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResultHolder;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.ProjectileWeaponItem;
import net.minecraft.world.item.TridentItem;
import net.minecraft.world.item.UseAnim;

import java.util.stream.Collectors;

/** Uses the item already held by the real maid; no inventory item is created. */
public final class HandUseAction extends AbstractMaidAction {
    private final InteractionHand hand;
    private int useTicks;
    private int fullUseDuration;
    private String stackBefore;
    private String inventoryBefore;
    private String bodyBefore;

    public HandUseAction(String requestId, String type, int timeout, InteractionHand hand) {
        super(requestId, type, timeout);
        this.hand = hand;
    }

    @Override
    protected void onStart(ActionContext context) {
        ItemStack stack = context.maid().getItemInHand(hand);
        if (stack.isEmpty()) {
            fail("EMPTY_HAND");
            return;
        }
        stackBefore = stackFingerprint(stack);
        inventoryBefore = context.inventory().snapshot(context.maid()).toString();
        bodyBefore = bodyFingerprint(context.maid());
        if (requiresPlayerContext(stack)) {
            HandUseOutcome.Value outcome = HandUseOutcome.duration(true, false, false, false);
            data.addProperty("use_outcome", outcome.name());
            fail(outcome.name());
            return;
        }
        UseAnim animation = stack.getUseAnimation();
        fullUseDuration = stack.getUseDuration();
        if (fullUseDuration > 0 && animation != UseAnim.NONE) {
            useTicks = Math.max(1, Math.min(timeoutTicks, fullUseDuration));
            try {
                context.maid().startUsingItem(hand);
                data.addProperty("use_animation", animation.name());
                data.addProperty("hand", hand.name());
            } catch (RuntimeException error) {
                data.addProperty("error", error.getClass().getSimpleName());
                fail("FAILED");
            }
            return;
        }
        // Run the item's real immediate-use entry point. EntityMaid is not replaced by
        // a fake Player; Player-only items reject explicitly instead of being simulated.
        try {
            InteractionResultHolder<ItemStack> result = stack.use(context.level(), null, hand);
            data.addProperty("hand", hand.name());
            data.addProperty("interaction_result", result.getResult().name());
            context.maid().setItemInHand(hand, result.getObject());
            HandUseOutcome.Value outcome = HandUseOutcome.immediate(
                    false, false, result.getResult().consumesAction(), hasItemStateChange(context));
            data.addProperty("use_outcome", outcome.name());
            if (outcome == HandUseOutcome.Value.SUCCESS) {
                context.maid().swing(hand);
                data.addProperty("verified_by", "minecraft_interaction_result");
                succeed("ITEM_USED");
            } else {
                fail(outcome.name());
            }
        } catch (NullPointerException | ClassCastException playerOnly) {
            fail("PLAYER_CONTEXT_REQUIRED");
        } catch (RuntimeException error) {
            data.addProperty("error", error.getClass().getSimpleName());
            fail("FAILED");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (context.gameTick() - startTick < useTicks) return;
        ItemStack stack = context.maid().getItemInHand(hand);
        if (stack.isEmpty()) {
            fail("ITEM_GONE");
            return;
        }
        UseAnim animation = stack.getUseAnimation();
        boolean activeEffect = animation == UseAnim.BLOCK && context.maid().isBlocking();
        try {
            if (animation == UseAnim.EAT || animation == UseAnim.DRINK || useTicks >= fullUseDuration) {
                ItemStack result = stack.finishUsingItem(context.level(), context.maid());
                context.maid().setItemInHand(hand, result);
            } else {
                stack.releaseUsing(context.level(), context.maid(), Math.max(0, fullUseDuration - useTicks));
                context.maid().setItemInHand(hand, stack);
            }
        } catch (NullPointerException | ClassCastException playerOnly) {
            context.maid().stopUsingItem();
            fail("PLAYER_CONTEXT_REQUIRED");
            return;
        } catch (RuntimeException error) {
            context.maid().stopUsingItem();
            data.addProperty("error", error.getClass().getSimpleName());
            fail("FAILED");
            return;
        }
        context.maid().stopUsingItem();
        data.addProperty("hand", hand.name());
        data.addProperty("use_animation", animation.name());
        boolean itemStateChanged = hasItemStateChange(context);
        boolean bodyStateChanged = !bodyBefore.equals(bodyFingerprint(context.maid()));
        data.addProperty("item_state_changed", itemStateChanged);
        data.addProperty("body_state_changed", bodyStateChanged);
        // A body delta alone can be caused by hunger, combat, or another world Tick.
        // Require item/inventory evidence, or a concrete active blocking state.
        boolean observableChange = activeEffect || itemStateChanged;
        HandUseOutcome.Value outcome = HandUseOutcome.duration(false, false, true, observableChange);
        data.addProperty("use_outcome", outcome.name());
        if (outcome == HandUseOutcome.Value.SUCCESS) {
            data.addProperty("verified_by", activeEffect ? "entity_active_effect" : "state_delta");
            succeed("ITEM_USED");
        } else {
            fail(outcome.name());
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.maid().stopUsingItem();
    }

    private boolean hasItemStateChange(ActionContext context) {
        return !stackBefore.equals(stackFingerprint(context.maid().getItemInHand(hand)))
                || !inventoryBefore.equals(context.inventory().snapshot(context.maid()).toString());
    }

    private static boolean requiresPlayerContext(ItemStack stack) {
        return stack.getItem() instanceof ProjectileWeaponItem
                || stack.getItem() instanceof TridentItem;
    }

    private static String stackFingerprint(ItemStack stack) {
        return stack.save(new CompoundTag()).toString();
    }

    private static String bodyFingerprint(EntityMaid maid) {
        String effects = maid.getActiveEffects().stream()
                .map(effect -> effect.getEffect().getDescriptionId() + ":" + effect.getAmplifier())
                .sorted().collect(Collectors.joining(","));
        return Float.floatToIntBits(maid.getHealth()) + "|" + maid.getHunger() + "|" + effects;
    }
}
