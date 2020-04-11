"""Run and higher level workflows in FSL."""
# pylint: disable=R0913, R0914
from pathlib import Path
from nipype.pipeline import engine as pe
from nipype.interfaces import fsl
from nipype.interfaces.utility import Function, IdentityInterface, Merge
from nipype.algorithms import modelgen, rapidart as ra
# from bids import BIDSLayout
from ..interfaces.bids import BIDSDataGrabber, BIDSDataSink
from ..interfaces.modelgen import GetRunModelInfo, GenerateHigherInfo
from ..interfaces.io import MergeAll, CollateWithMetadata
from ..interfaces.visualization import PlotMatrices
from .. import utils


def fsl_run_level_wf(model,
                     step,
                     bids_dir,
                     output_dir,
                     work_dir,
                     subject_id,
                     database_path,
                     smoothing_fwhm=None,
                     smoothing_level=None,
                     smoothing_type=None,
                     use_rapidart=False,
                     detrend_poly=None,
                     align_volumes=None,
                     name='fsl_run_level_wf'):
    """Generate run level workflow for a given model."""
    bids_dir = Path(bids_dir)
    work_dir = Path(work_dir)
    workflow = pe.Workflow(name=name)
    # layout = BIDSLayout.load(database_path)
    level = step['Level']
    if smoothing_type == 'iso':
        dimensionality = 3
    elif smoothing_type == 'inp':
        dimensionality = 2

    workflow.__desc__ = ""
    (work_dir / model['Name']).mkdir(exist_ok=True)

    getter = pe.Node(
        BIDSDataGrabber(
            subject=subject_id,
            database_path=database_path,
            output_query={
                'bold_files': {**{'datatype': 'func', 'desc': 'preproc',
                                  'extension': 'nii.gz', 'suffix': 'bold'},
                               **model['Input']['Include']}}),
        name='func_select')

    get_info = pe.MapNode(
        GetRunModelInfo(
            model=step, bids_dir=bids_dir, database_path=database_path,
            detrend_poly=detrend_poly, align_volumes=align_volumes),
        iterfield=['functional_file'],
        name=f'get_{level}_info')

    realign_runs = pe.MapNode(
        fsl.MCFLIRT(interpolation='sinc'),
        iterfield=['in_file', 'ref_file'],
        name='func_realign')

    specify_model = pe.MapNode(
        modelgen.SpecifyModel(
            high_pass_filter_cutoff=-1.0, input_units='secs'),
        iterfield=['functional_runs', 'subject_info', 'time_repetition'],
        name=f'model_{level}_specify')

    fit_model = pe.MapNode(
        IdentityInterface(fields=['session_info', 'interscan_interval',
                                  'contrasts', 'functional_data'],
                          mandatory_inputs=True),
        iterfield=['functional_data', 'session_info',
                   'interscan_interval', 'contrasts'],
        name=f'model_{level}_fit')

    first_level_design = pe.MapNode(
        fsl.Level1Design(bases={'dgamma': {'derivs': False}},
                         model_serial_correlations=False),
        iterfield=['session_info', 'interscan_interval', 'contrasts'],
        name=f'model_{level}_design')

    generate_model = pe.MapNode(
        fsl.FEATModel(environ={'FSLOUTPUTTYPE': 'NIFTI_GZ'},
                      output_type='NIFTI_GZ'),
        iterfield=['fsf_file', 'ev_files'],
        name=f'model_{level}_generate')

    estimate_model = pe.MapNode(
        fsl.FILMGLS(environ={'FSLOUTPUTTYPE': 'NIFTI_GZ'},
                    mask_size=5, threshold=0.0,
                    # smooth_autocorr=True
                    output_type='NIFTI_GZ', results_dir='results',
                    autocorr_noestimate=True),
        iterfield=['design_file', 'in_file', 'tcon_file'],
        name=f'model_{level}_estimate')

    image_pattern = ('[sub-{subject}/][ses-{session}/]'
                     '[sub-{subject}_][ses-{session}_]'
                     'task-{task}_[acq-{acquisition}_]'
                     '[rec-{reconstruction}_][run-{run}_]'
                     '[echo-{echo}_][space-{space}_]contrast-{contrast}_'
                     'stat-{stat<effect|variance|z|p|t|F>}_statmap.nii.gz')

    run_rapidart = pe.MapNode(
        ra.ArtifactDetect(use_differences=[True, False], use_norm=True,
                          zintensity_threshold=3, norm_threshold=1,
                          bound_by_brainmask=True, mask_type='file',
                          parameter_source='FSL'),
        iterfield=['realignment_parameters', 'realigned_files', 'mask_file'],
        name='rapidart_run')

    reshape_rapidart = pe.MapNode(
        Function(input_names=['run_info', 'functional_file',
                              'outlier_file', 'contrast_entities'],
                 output_names=['run_info', 'contrast_entities'],
                 function=utils.reshape_ra),
        iterfield=['run_info', 'functional_file',
                   'outlier_file', 'contrast_entities'],
        name='reshape_rapidart')

    mean_img = pe.MapNode(
        fsl.ImageMaths(op_string='-Tmean', suffix='_mean'),
        iterfield=['in_file', 'mask_file'],
        name='smooth_susan_avgimg')

    median_img = pe.MapNode(
        fsl.ImageStats(op_string='-k %s -p 50'),
        iterfield=['in_file', 'mask_file'],
        name='smooth_susan_medimg')

    merge = pe.Node(
        Merge(2, axis='hstack'),
        name='smooth_merge')

    run_susan = pe.MapNode(
        fsl.SUSAN(fwhm=smoothing_fwhm, dimension=dimensionality),
        iterfield=['in_file', 'brightness_threshold', 'usans'],
        name='smooth_susan')
    # Exists solely to correct undesirable behavior of FSL
    # that results in loss of constant columns
    correct_matrices = pe.MapNode(
        Function(input_names=['design_matrix'],
                 output_names=['design_matrix'],
                 function=utils.correct_matrix),
        iterfield=['design_matrix'],
        run_without_submitting=True,
        name=f'correct_{level}_matrices')

    collate = pe.Node(
        MergeAll(
            fields=[
                'effect_maps', 'variance_maps', 'tstat_maps',
                'zscore_maps', 'contrast_metadata'],
            check_lengths=True),
        name=f'collate_{level}')

    collate_outputs = pe.Node(
        CollateWithMetadata(
            fields=[
                'effect_maps', 'variance_maps', 'tstat_maps', 'zscore_maps'],
            field_to_metadata_map={
                'effect_maps': {'stat': 'effect'},
                'variance_maps': {'stat': 'variance'},
                # 'pvalue_maps': {'stat': 'p'},
                'zscore_maps': {'stat': 'z'},
                'tstat_maps': {'stat': 't'}}),
        name=f'collate_{level}_outputs')

    plot_matrices = pe.MapNode(
        PlotMatrices(output_dir=output_dir),
        iterfield=['mat_file', 'con_file', 'entities', 'run_info'],
        run_without_submitting=True,
        name=f'plot_{level}_matrices')

    ds_contrast_maps = pe.MapNode(
        BIDSDataSink(base_directory=output_dir,
                     path_patterns=image_pattern),
        iterfield=['entities', 'in_file'],
        run_without_submitting=True,
        name=f'ds_{level}_contrast_maps')

    wrangle_outputs = pe.Node(
        IdentityInterface(
            fields=['contrast_metadata', 'contrast_maps']),
        name=f'wrangle_{level}_outputs')

    # Setup connections among nodes
    workflow.connect([
        (getter, realign_runs, [('bold_files', 'in_file')]),
        (getter, get_info, [('bold_files', 'functional_file')]),
        (get_info, realign_runs, [('reference_image', 'ref_file')]),
    ])

    if use_rapidart:
        workflow.connect([
            (get_info, run_rapidart, [
                ('motion_parameters', 'realignment_parameters')]),
            (get_info, run_rapidart, [('brain_mask', 'mask_file')]),
            (realign_runs, run_rapidart, [('out_file', 'realigned_files')]),
            (run_rapidart, reshape_rapidart, [
                ('outlier_files', 'outlier_file')]),
            (get_info, reshape_rapidart, [
                ('run_info', 'run_info'),
                ('contrast_entities', 'contrast_entities')]),
            (realign_runs, reshape_rapidart, [
                ('out_file', 'functional_file')]),
            (reshape_rapidart, specify_model, [('run_info', 'subject_info')]),
            (reshape_rapidart, plot_matrices, [('run_info', 'run_info')]),
            (reshape_rapidart, collate, [
                ('contrast_entities', 'contrast_metadata')])
        ])
    else:
        workflow.connect([
            (get_info, specify_model, [('run_info', 'subject_info')]),
            (get_info, plot_matrices, [('run_info', 'run_info')]),
            (get_info, collate, [
                ('contrast_entities', 'contrast_metadata')])
        ])

    if smoothing_level == 'l1':
        workflow.connect([
            (realign_runs, mean_img, [('out_file', 'in_file')]),
            (realign_runs, median_img, [('out_file', 'in_file')]),
            (get_info, mean_img, [('brain_mask', 'mask_file')]),
            (get_info, median_img, [('brain_mask', 'mask_file')]),
            (mean_img, merge, [('out_file', 'in1')]),
            (median_img, merge, [('out_stat', 'in2')]),
            (realign_runs, run_susan, [('out_file', 'in_file')]),
            (median_img, run_susan, [
                (('out_stat', utils.get_btthresh), 'brightness_threshold')]),
            (merge, run_susan, [(('out', utils.get_usans), 'usans')]),
            (run_susan, specify_model, [('smoothed_file', 'functional_runs')]),
            (run_susan, fit_model, [('smoothed_file', 'functional_data')])
        ])
    else:
        workflow.connect([
            (realign_runs, specify_model, [('out_file', 'functional_runs')]),
            (realign_runs, fit_model, [('out_file', 'functional_data')])
        ])

    workflow.connect([
        (get_info, specify_model, [('repetition_time', 'time_repetition')]),

        (specify_model, fit_model, [('session_info', 'session_info')]),
        (get_info, fit_model, [('repetition_time', 'interscan_interval'),
                               ('run_contrasts', 'contrasts')]),

        (fit_model, first_level_design, [
            ('interscan_interval', 'interscan_interval'),
            ('session_info', 'session_info'),
            ('contrasts', 'contrasts')]),
        (first_level_design, generate_model, [('fsf_files', 'fsf_file')]),
        (first_level_design, generate_model, [('ev_files', 'ev_files')]),
    ])
    if detrend_poly:
        workflow.connect([
            (generate_model, correct_matrices, [
                ('design_file', 'design_matrix')]),
            (correct_matrices, plot_matrices, [
                ('design_matrix', 'mat_file')]),
            (correct_matrices, estimate_model, [
                ('design_matrix', 'design_file')])
        ])
    else:
        workflow.connect([
            (generate_model, plot_matrices, [('design_file', 'mat_file')]),
            (generate_model, estimate_model, [('design_file', 'design_file')]),
        ])

    workflow.connect([
        (get_info, plot_matrices, [('run_entities', 'entities')]),
        (generate_model, plot_matrices, [('con_file', 'con_file')]),

        (fit_model, estimate_model, [('functional_data', 'in_file')]),
        (generate_model, estimate_model, [('con_file', 'tcon_file')]),

        (estimate_model, collate, [('copes', 'effect_maps'),
                                   ('varcopes', 'variance_maps'),
                                   ('tstats', 'tstat_maps'),
                                   ('zstats', 'zscore_maps')]),

        (collate, collate_outputs, [('effect_maps', 'effect_maps'),
                                    ('variance_maps', 'variance_maps'),
                                    ('tstat_maps', 'tstat_maps'),
                                    ('zscore_maps', 'zscore_maps'),
                                    ('contrast_metadata', 'metadata')]),

        (collate_outputs, ds_contrast_maps, [('out', 'in_file'),
                                             ('metadata', 'entities')]),

        (collate_outputs, wrangle_outputs, [('metadata', 'contrast_metadata'),
                                            ('out', 'contrast_maps')]),
    ])

    return workflow


def fsl_higher_level_wf(output_dir,
                        work_dir,
                        step,
                        database_path,
                        # smoothing_fwhm=None,
                        # smoothing_type=None,
                        align_volumes=None,
                        smoothing_level=None,
                        name='fsl_higher_level_wf'):
    """
    Produce a second level (across runs) workflow for a given subject.

    This workflow generates processes functional_data across a
    single session (read: between runs) and computes
    effects, variances, residuals and statmaps
    using FSLs FLAME0 given information in the bids model file
    """
    workflow = pe.Workflow(name=name)
    workflow.base_dir = work_dir
    workflow.desc = ""

    # layout = BIDSLayout.load(database_path)
    level = step['Level']

    image_pattern = ('[sub-{subject}/][ses-{session}/]'
                     '[sub-{subject}_][ses-{session}_]task-{task}_'
                     '[acq-{acquisition}_][rec-{reconstruction}_]'
                     '[echo-{echo}_][space-{space}_]contrast-{contrast}_'
                     'stat-{stat<effect|variance|z|p|t|F>}_statmap.nii.gz')

    wrangle_inputs = pe.Node(
        IdentityInterface(
            fields=['contrast_metadata', 'contrast_maps']),
        name=f'wrangle_{level}_inputs')

    get_info = pe.Node(
        GenerateHigherInfo(
            model=step,
            database_path=database_path,
            align_volumes=align_volumes),
        name=f'get_{level}_info')
    if smoothing_level == 'l2':
        pass

    estimate_model = pe.MapNode(
        fsl.FLAMEO(run_mode='fe'),
        iterfield=['design_file', 't_con_file', 'mask_file',
                   'cov_split_file', 'dof_var_cope_file',
                   'var_cope_file', 'cope_file'],
        name=f'model_{level}_estimate')

    collate = pe.Node(
        MergeAll(['effect_maps', 'variance_maps', 'tstat_maps',
                  'zscore_maps', 'contrast_metadata'],
                 check_lengths=False),
        name=f'collate_{level}_level')

    collate_outputs = pe.Node(
        CollateWithMetadata(
            fields=[
                'effect_maps', 'variance_maps', 'tstat_maps', 'zscore_maps'],
            field_to_metadata_map={
                'effect_maps': {'stat': 'effect'},
                'variance_maps': {'stat': 'variance'},
                # 'pvalue_maps': {'stat': 'p'},
                'zscore_maps': {'stat': 'z'},
                'tstat_maps': {'stat': 't'}
            }),
        name=f'collate_{level}_outputs')

    ds_contrast_maps = pe.MapNode(
        BIDSDataSink(base_directory=output_dir,
                     path_patterns=image_pattern),
        iterfield=['entities', 'in_file'],
        run_without_submitting=True,
        name=f'ds_{level}_contrast_maps')

    wrangle_outputs = pe.Node(
        IdentityInterface(
            fields=['contrast_metadata', 'contrast_maps', 'brain_mask']),
        name=f'wrangle_{level}_outputs')

    workflow.connect([
        (wrangle_inputs, get_info, [
            ('contrast_metadata', 'contrast_metadata'),
            ('contrast_maps', 'contrast_maps')]),
        (get_info, estimate_model, [
            ('design_matrices', 'design_file'),
            ('contrast_matrices', 't_con_file'),
            ('covariance_matrices', 'cov_split_file'),
            ('dof_maps', 'dof_var_cope_file'),
            ('variance_maps', 'var_cope_file'),
            ('effect_maps', 'cope_file'),
            ('brain_mask', 'mask_file')]),
        (estimate_model, collate, [
            ('copes', 'effect_maps'),
            ('var_copes', 'variance_maps'),
            ('tstats', 'tstat_maps'),
            ('zstats', 'zscore_maps')]),
        (get_info, collate, [('contrast_metadata', 'contrast_metadata')]),
        (collate, collate_outputs, [
            ('effect_maps', 'effect_maps'),
            ('variance_maps', 'variance_maps'),
            ('tstat_maps', 'tstat_maps'),
            ('zscore_maps', 'zscore_maps'),
            ('contrast_metadata', 'metadata')]),
        (collate_outputs, ds_contrast_maps, [
            ('out', 'in_file'),
            ('metadata', 'entities')]),
        (collate_outputs, wrangle_outputs, [
            ('metadata', 'contrast_metadata'),
            ('out', 'contrast_maps')])
    ])

    return workflow
