"""FSL based interfaces that break when using testing."""
from nipype.interfaces.fsl.maths import MathsInput, MathsCommand
from nipype.interfaces.base import File


class _ApplyMaskInput(MathsInput):

    mask_file = File(
        mandatory=True,
        argstr="-mas %s",
        position=4,
        desc="binary image defining mask space")


class ApplyMask(MathsCommand):
    """Use fslmaths to apply a binary mask to another image."""

    input_spec = _ApplyMaskInput
    _suffix = "_masked"
