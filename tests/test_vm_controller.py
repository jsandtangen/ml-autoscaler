from scaler.infrastructure.vm_controller import FakeVMController


def test_fake_vm_controller():
    controller = FakeVMController()

    assert controller.running_instances() == 0

    controller.scale_up(3)
    assert controller.running_instances() == 3

    controller.scale_down(1)
    assert controller.running_instances() == 2

    controller.scale_down(10)
    assert controller.running_instances() == 0