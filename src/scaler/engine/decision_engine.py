class DecisionEngine:
    def __init__(self, strategy, vm_controller):
        self.strategy = strategy
        self.vm_controller = vm_controller

    def evaluate(self, player_count):
        desired = self.strategy.desired_instances(player_count)
        return self.apply_desired_instances(desired)

    def apply_desired_instances(self, desired):
        current = self.vm_controller.running_instances()

        if desired > current:
            self.vm_controller.scale_up(desired - current)

        elif desired < current:
            self.vm_controller.scale_down(current - desired)

        return self.vm_controller.running_instances()
