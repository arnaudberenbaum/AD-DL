import numpy as np


def find_image_path(caps_dir, participant_id, session_id, preprocessing, group=None):
    from os import path
    if preprocessing == "linear":
        image_path = path.join(caps_dir, 'subjects', participant_id, session_id,
                               't1', 'preprocessing_dl',
                               participant_id + '_' + session_id +
                               '_space-MNI_res-1x1x1.nii.gz')
    elif preprocessing == "extensive":
        image_path = path.join(caps_dir, 'subjects', participant_id, session_id,
                               't1', 'spm', 'segmentation', 'normalized_space',
                               participant_id + '_' + session_id +
                               '_T1w_segm-graymatter_space-Ixi549Space_modulated-off_probability.nii.gz')

    elif preprocessing == "dartel":
        if group is None:
            raise ValueError("A group value must be given with dartel preprocessing")
        image_path = path.join(caps_dir, 'subjects', participant_id, session_id,
                               't1', 'spm', 'dartel', group,
                               participant_id + '_' + session_id +
                               '_T1w_segm-graymatter_space-Ixi549Space_modulated-on_probability.nii.gz')
    else:
        raise ValueError("Preprocessing %s must be in ['linear', 'extensive']." % preprocessing)

    return image_path


def binary_t1_pgm(im_data):
    """
    :param im_data: probability gray maps
    :return: binarized probability gray maps
    """
    m = im_data > 0.0
    m = m.astype('float32')
    return m


def im_loss_roi_gaussian_distribution(im_data, atlas_to_mask, min_value):
    gm_masked = np.array(im_data, copy=True)
    gm_masked[atlas_to_mask == 0] = 0

    gm_loss = np.array(gm_masked, copy=True)
    idx = np.nonzero(gm_masked)
    coordinates = []
    for i in range(len(idx[0])):
        coordinates.append([idx[0][i], idx[1][i], idx[2][i]])
    length_coordinates = len(coordinates)  # all the non zero values

    n = np.random.normal(loc=0.0, scale=0.1, size=length_coordinates)  # gaussian distribution with std = 0.1 and media = 0
    max_value = np.min(n)
    n_new = n + abs(max_value)

    n_diff = n_new * 10 + min_value
    for i, coord in enumerate(coordinates):
        x, y, z = coord
        gm_loss[x, y, z] = gm_masked[x, y, z] - n_diff[i] * (gm_masked[x, y, z]) / 100

    normal_region = np.array(im_data, copy=True)
    normal_region[atlas_to_mask > 0] = 0

    im_with_loss_gm_roi = normal_region + gm_loss

    return im_with_loss_gm_roi


def find_borders_of_one_roi(output_path, i, tsv_atlas):
    """

    :param output_path: where I have saved my borders
    :param i: index of the ROI
    :param tsv_atlas: tsv atlas
    :return: mask with 1 where there are the borders of roi 'i' with the other regions
    """
    import nibabel as nib
    import os

    total_mask = []

    border_1 = nib.load(os.path.join(output_path, 'borders-' + str(i) + '.nii')).get_data()
    for j in set(tsv_atlas.label):
        if j not in [0, i]:
            border_2 = nib.load(os.path.join(output_path, 'borders-' + str(j) + '.nii')).get_data()
            image_sum = border_1 + border_2
            mask = image_sum > 1
            mask = mask.astype('float32')
            total_mask.append(mask)

    first_mask = total_mask[0]
    for l in total_mask[1:]:
        first_mask = first_mask + l
    first_mask[first_mask > 0] = 1

    return first_mask


def find_border_of_prob_gm_and_atlas(output_path, i, tsv_atlas, im_data):
    """

    :param output_path:
    :param i:
    :param tsv_atlas:
    :param im_data:
    :return:
    """
    mask_of_ROI_AAL2 = find_borders_of_one_roi(output_path, i, tsv_atlas)
    gm = binary_t1_pgm(im_data)
    final_mask_for_gaussian = mask_of_ROI_AAL2 + gm  # AAL2 borders between the ROIS + T1w GM
    f_g = final_mask_for_gaussian > 1
    f_g = f_g.astype('float32')

    idx = np.nonzero(f_g)
    coordinates = []
    for i in range(len(idx[0])):
        coordinates.append([idx[0][i], idx[1][i], idx[2][i]])
    return coordinates
