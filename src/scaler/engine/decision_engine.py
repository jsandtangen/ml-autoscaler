from .scaling_limits import ScalingLimits


class DecisionEngine:
    def __init__(self, strategy, vm_controller, limits=None, capacity_policy=None):
        self.strategy = strategy
        self.vm_controller = vm_controller
        self.limits = limits or ScalingLimits()
        self.capacity_policy = capacity_policy
        self.last_plan = None

    def evaluate(self, player_count):
        desired = self.desired_instances(player_count)
        return self.apply_desired_instances(desired)

    def desired_instances(self, player_count, current=None):
        contextual = getattr(self.strategy, "desired_for_current", None)
        if contextual:
            if current is None:
                current = self.vm_controller.running_instances()
            return contextual(player_count, current)
        return self.strategy.desired_instances(player_count)

    def apply_desired_instances(self, desired):
        current = self.vm_controller.running_instances()
        self.last_plan = self.limits.plan(desired, current)
        target = self.last_plan.cycle_target_instances
        if target > current:
            self.vm_controller.scale_up(target - current)
        elif target < current:
            self.vm_controller.scale_down(current - target)

        return self.vm_controller.running_instances()
