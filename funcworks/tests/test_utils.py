"""Tests for utils."""
import numpy as np
from funcworks import utils


def test__get_btthresh():
    """Test get_btthresh."""
    median_vals = np.arange(0, 10, 0.1).tolist()
    output = utils.get_btthresh(median_vals)
    assert len(median_vals) == len(output)


def test__get_usans():
    """Test get_usans."""
    median_vals = np.arange(0, 10, 0.1).tolist()
    mean_vals = np.arange(0, 10, 0.1).tolist()
    output = utils.get_usans(zip(median_vals, mean_vals))
    assert len(median_vals) == len(output)


def test__snake_to_camel():
    """Test snake_to_camel."""
    expected = 'trialType.negLureFa'
    input = 'trial_type.neg_lure_fa'
    output = utils.snake_to_camel(input)
    assert output == expected


def test__reshape_ra():
    """Test reshape_ra."""
    from nipype.interfaces.base import Bunch
    import nibabel as nb
    from pathlib import Path
    import numpy as np

    run_info = Bunch(**{'Regressors': []})
    outlier_file = Path.cwd() / 'outlier_test.txt'
    np.savetxt(outlier_file, np.array([0], [1], [36], [54], [60], [75]))
    test_img = nb.nifti1.Nifti1Image(np.ones(90, 90, 90), np.eye(4))
    test_img.save(Path.cwd() / 'test.nii.gz')
    contrast_entities = {'DegreesOfFreedom': 9}
    (output_run_info,
     output_contrast_entities) = utils.reshape_ra(
         run_info, test_img, outlier_file, contrast_entities)
    assert output_contrast_entities['DegreesOfFreedom'] == 3
    assert len(output_run_info.regressors) == 6
    for col in output_run_info.regressors:
        assert np.sum(col) == 1
