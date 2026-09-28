class FakeVMController:
    def __init__(self):
        self.instances = 0

    def running_instances(self):
        return self.instances

    def scale_up(self, count):
        self.instances += count

    def scale_down(self, count):
        self.instances = max(0, self.instances - count)