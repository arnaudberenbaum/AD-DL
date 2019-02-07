import torch
import pandas as pd
import numpy as np
import os
from os import path
from torch.utils.data import Dataset, sampler
from sklearn.model_selection import StratifiedShuffleSplit, StratifiedKFold
from scipy.ndimage.filters import gaussian_filter
import warnings


class MRIDataset(Dataset):
    """Dataset of MRI organized in a CAPS folder."""

    def __init__(self, img_dir, data_file, data_path='linear', transform=None):
        """
        Args:
            img_dir (string): Directory of all the images.
            data_file (string): File name of the train/test split file.
            data_path (string): Defines the path to the data in CAPS
            transform (callable, optional): Optional transform to be applied on a sample.

        """
        self.img_dir = img_dir
        self.transform = transform
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1, 'unlabeled': -1}
        self.data_path = data_path

        # Check the format of the tsv file here
        if isinstance(data_file, str):
            self.df = pd.read_csv(data_file, sep='\t')
        elif isinstance(data_file, pd.DataFrame):
            self.df = data_file
        else:
            raise Exception('The argument datafile is not of correct type.')

        if ('diagnosis' not in list(self.df.columns.values)) or ('session_id' not in list(self.df.columns.values)) or \
           ('participant_id' not in list(self.df.columns.values)):
            raise Exception("the data file is not in the correct format."
                            "Columns should include ['participant_id', 'session_id', 'diagnosis']")

        self.size = self[0]['image'].numpy().size

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        img_name = self.df.loc[idx, 'participant_id']
        img_label = self.df.loc[idx, 'diagnosis']
        sess_name = self.df.loc[idx, 'session_id']
        # Not in BIDS but in CAPS
        if self.data_path == "linear":
            image_path = path.join(self.img_dir, 'subjects', img_name, sess_name,
                                   't1', 'preprocessing_dl',
                                   img_name + '_' + sess_name + '_space-MNI_res-1x1x1.pt')
        elif self.data_path == "dartel":
            image_path = path.join(self.img_dir, 'subjects', img_name, sess_name,
                                   't1', 'spm', 'dartel', 'group-ADNI-bl',
                                   img_name + '_' + sess_name + '.pt')
            raise NotImplementedError("Dartel output has not been computed yet.")
        elif self.data_path == "mni":
            image_path = path.join(self.img_dir, 'subjects', img_name, sess_name,
                                   't1', 'spm', 'segmentation', 'normalized_space',
                                   img_name + '_' + sess_name + '_space-Ixi549Space_T1w.pt')
        else:
            raise NotImplementedError("The data path %s is not implemented" % self.data_path)

        image = torch.load(image_path)
        label = self.diagnosis_code[img_label]

        if self.transform:
            image = self.transform(image)
        sample = {'image': image, 'label': label, 'participant_id': img_name, 'session_id': sess_name}

        return sample

    def session_restriction(self, session):
        """
            Allows to generate a new MRIDataset using some specific sessions only (mostly used for evaluation of test)

            :param session: (str) the session wanted. Must be 'all' or 'ses-MXX'
            :return: (DataFrame) the dataset with the wanted sessions
            """
        from copy import copy

        data_output = copy(self)
        if session == "all":
            return data_output
        else:
            df_session = self.df[self.df.session_id == session]
            df_session.reset_index(drop=True, inplace=True)
            data_output.df = df_session
            if len(data_output) == 0:
                raise Exception("The session %s doesn't exist for any of the subjects in the test data" % session)
            return data_output


class GaussianSmoothing(object):

    def __init__(self, sigma):
        self.sigma = sigma

    def __call__(self, sample):
        image = sample['image']
        np.nan_to_num(image, copy=False)
        smoothed_image = gaussian_filter(image, sigma=self.sigma)
        sample['image'] = smoothed_image

        return sample


class ToTensor(object):
    """Convert image type to Tensor and diagnosis to diagnosis code"""

    def __call__(self, image):
        np.nan_to_num(image, copy=False)
        image = image.astype(float)

        return torch.from_numpy(image[np.newaxis, :]).float()


class MinMaxNormalization(object):
    """Normalizes a tensor between 0 and 1"""

    def __call__(self, image):
        return (image - image.min()) / (image.max() - image.min())


def generate_sampler(dataset, sampler_option='random'):
    """
    Returns sampler according to the wanted options

    :param dataset: (MRIDataset) the dataset to sample from
    :param sampler_option: (str) choice of sampler
    :return: (Sampler)
    """
    count = [0, 0]
    diagnoses_df = dataset.df
    for idx in diagnoses_df.index:
        diagnosis = diagnoses_df.loc[idx, "diagnosis"]
        key = dataset.diagnosis_code[diagnosis]
        count[key] += 1

    weight_per_class = 1 / np.array(count)
    weights = [0] * len(diagnoses_df)

    for idx, diagnosis in enumerate(diagnoses_df.diagnosis.values):
        key = dataset.diagnosis_code[diagnosis]
        weights[idx] = weight_per_class[key]

    weights = torch.DoubleTensor(weights)

    if sampler_option == 'random':
        s = sampler.RandomSampler(dataset)
    elif sampler_option == 'weighted':
        s = sampler.WeightedRandomSampler(weights, len(weights))
    else:
        raise NotImplementedError("The option %s for sampler is not implemented" % sampler_option)

    return s


def subject_diagnosis_df(subject_session_df):
    """
    Creates a DataFrame with only one occurence of each subject and the most early diagnosis
    Some subjects may not have the baseline diagnosis (ses-M00 doesn't exist)

    :param subject_session_df: (DataFrame) a DataFrame with columns containing 'participant_id', 'session_id', 'diagnosis'
    :return: DataFrame with the same columns as the input
    """
    temp_df = subject_session_df.set_index(['participant_id', 'session_id'])
    subjects_df = pd.DataFrame(columns=subject_session_df.columns)
    for subject, subject_df in temp_df.groupby(level=0):
        session_nb_list = [int(session[5::]) for _, session in subject_df.index.values]
        session_nb_list.sort()
        session_baseline_nb = session_nb_list[0]
        if session_baseline_nb < 10:
            session_baseline = 'ses-M0' + str(session_baseline_nb)
        else:
            session_baseline = 'ses-M' + str(session_baseline_nb)
        row_baseline = list(subject_df.loc[(subject, session_baseline)])
        row_baseline.insert(0, subject)
        row_baseline.insert(1, session_baseline)
        row_baseline = np.array(row_baseline).reshape(1, len(row_baseline))
        row_df = pd.DataFrame(row_baseline, columns=subject_session_df.columns)
        subjects_df = subjects_df.append(row_df)

    subjects_df.reset_index(inplace=True, drop=True)
    return subjects_df


def multiple_time_points(df, subset_df):
    """
    Returns a DataFrame with all the time points of each subject

    :param df: (DataFrame) the reference containing all the time points of all subjects.
    :param subset_df: (DataFrame) the DataFrame containing the subset of subjects.
    :return: mtp_df (DataFrame) a DataFrame with the time points of the subjects of subset_df
    """
    mtp_df = pd.DataFrame(columns=df.columns)
    temp_df = df.set_index('participant_id')
    for idx in subset_df.index.values:
        subject = subset_df.loc[idx, 'participant_id']
        subject_df = temp_df.loc[subject]
        if isinstance(subject_df, pd.Series):
            subject_id = subject_df.name
            row = list(subject_df.values)
            row.insert(0, subject_id)
            subject_df = pd.DataFrame(np.array(row).reshape(1, len(row)), columns=df.columns)
            mtp_df = mtp_df.append(subject_df)
        else:
            mtp_df = mtp_df.append(subject_df.reset_index())

    mtp_df.reset_index(inplace=True, drop=True)
    return mtp_df


def old_split_subjects_to_tsv(diagnoses_tsv, n_splits=5, val_size=0.15):
    """
    Write the tsv files corresponding to the train/val/test splits of all folds

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param n_splits: (int) the number of splits wanted in the cross-validation
    :param val_size: (float) proportion of the train set being used for validation
    :return: None
    """

    df = pd.read_csv(diagnoses_tsv, sep='\t')
    if 'diagnosis' not in list(df.columns.values):
        raise Exception('Diagnoses file is not in the correct format.')
    # Here we reduce the DataFrame to have only one diagnosis per subject (multiple time points case)
    diagnosis_df = subject_diagnosis_df(df)
    diagnoses_list = list(diagnosis_df.diagnosis)
    unique = list(set(diagnoses_list))
    y = np.array([unique.index(x) for x in diagnoses_list])  # There is one label per diagnosis depending on the order

    splits = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=2)
    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'splits-' + str(n_splits))
    if not path.exists(sets_dir):
        os.makedirs(sets_dir)

    n_iteration = 0
    for train_index, test_index in splits.split(np.zeros(len(y)), y):

        y_train = y[train_index]
        diagnosis_df_train = diagnosis_df.loc[train_index]

        # split the train data into training and validation set
        skf_2 = StratifiedShuffleSplit(n_splits=1, test_size=val_size, random_state=2)
        indices = next(skf_2.split(np.zeros(len(y_train)), y_train))
        train_ind, valid_ind = indices

        df_sub_test = diagnosis_df.iloc[test_index]
        df_sub_valid = diagnosis_df_train.iloc[valid_ind]
        df_sub_train = diagnosis_df_train.iloc[train_ind]
        df_test = multiple_time_points(df, df_sub_test)
        df_valid = multiple_time_points(df, df_sub_valid)
        df_train = multiple_time_points(df, df_sub_train)

        df_train.to_csv(path.join(sets_dir, 'val_size-' + str(val_size) + '_iteration-' + str(n_iteration) + '_train.tsv'), sep='\t', index=False)
        df_test.to_csv(path.join(sets_dir, 'val_size-' + str(val_size) + '_iteration-' + str(n_iteration) + '_test.tsv'), sep='\t', index=False)
        df_valid.to_csv(path.join(sets_dir, 'val_size-' + str(val_size) + '_iteration-' + str(n_iteration) + '_valid.tsv'), sep='\t', index=False)
        n_iteration += 1


def split_subjects_to_tsv(diagnoses_tsv, val_size=0.15):
    """
    Write the tsv files corresponding to the train/val/test splits of all folds

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param val_size: (float) proportion of the train set being used for validation
    :return: None
    """

    df = pd.read_csv(diagnoses_tsv, sep='\t')
    if 'diagnosis' not in list(df.columns.values):
        raise Exception('Diagnoses file is not in the correct format.')
    # Here we reduce the DataFrame to have only one diagnosis per subject (multiple time points case)
    diagnosis_df = subject_diagnosis_df(df)
    diagnoses_list = list(diagnosis_df.diagnosis)
    unique = list(set(diagnoses_list))
    y = np.array([unique.index(x) for x in diagnoses_list])  # There is one label per diagnosis depending on the order

    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'val_size-' + str(val_size))
    if not path.exists(sets_dir):
        os.makedirs(sets_dir)

    # split the train data into training and validation set
    skf_2 = StratifiedShuffleSplit(n_splits=1, test_size=val_size, random_state=2)
    indices = next(skf_2.split(np.zeros(len(y)), y))
    train_ind, valid_ind = indices

    df_sub_valid = diagnosis_df.iloc[valid_ind]
    df_sub_train = diagnosis_df.iloc[train_ind]
    df_valid = multiple_time_points(df, df_sub_valid)
    df_train = multiple_time_points(df, df_sub_train)

    df_valid.to_csv(path.join(sets_dir, 'valid.tsv'), sep='\t', index=False)
    df_train.to_csv(path.join(sets_dir, 'train.tsv'), sep='\t', index=False)


def old_load_split(diagnoses_tsv, fold, n_splits=5, val_size=0.15):
    """
    Returns the paths of the TSV files for each set

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param fold: (int) the number of the current fold
    :param n_splits: (int) the total number of folds
    :param val_size: (float) the proportion of the training set used for validation
    :return: 3 Strings
        training_tsv
        test_tsv
        valid_tsv
    """
    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'splits-' + str(n_splits))

    if fold >= n_splits:
        raise Exception("The fold number must not exceed the number of splits.")

    training_tsv = path.join(sets_dir,
                             'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_train.tsv')
    test_tsv = path.join(sets_dir,
                         'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_test.tsv')
    valid_tsv = path.join(sets_dir,
                          'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_valid.tsv')

    if not path.exists(training_tsv) or not path.exists(test_tsv) or not path.exists(valid_tsv):
        old_split_subjects_to_tsv(diagnoses_tsv, n_splits, val_size)

        training_tsv = path.join(sets_dir,
                                 'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_train.tsv')
        test_tsv = path.join(sets_dir,
                             'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_test.tsv')
        valid_tsv = path.join(sets_dir,
                              'val_size-' + str(val_size) + '_iteration-' + str(fold) + '_valid.tsv')

    return training_tsv, test_tsv, valid_tsv


def load_split(diagnoses_tsv, val_size=0.15):
    """
    Returns the paths of the TSV files for each set

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param val_size: (float) the proportion of the training set used for validation
    :return: 3 Strings
        training_tsv
        valid_tsv
    """
    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'val_size-' + str(val_size))

    training_tsv = path.join(sets_dir, 'train.tsv')
    valid_tsv = path.join(sets_dir, 'valid.tsv')

    if not path.exists(training_tsv) or not path.exists(valid_tsv):
        split_subjects_to_tsv(diagnoses_tsv, val_size)

        training_tsv = path.join(sets_dir, 'train.tsv')
        valid_tsv = path.join(sets_dir, 'valid.tsv')

    return training_tsv, valid_tsv


def load_autoencoder_data(train_val_path, diagnoses_list, baseline=True):
    """
    Creates a DataFrame for training and validation sets given the wanted diagnoses

    :param train_val_path: Path to the train / val decomposition
    :param diagnoses_list: list of diagnoses to select to construct the DataFrames
    :param baseline: bool choose to use baseline only instead of all data available
    :return:
        train_df DataFrame with training data
        valid_df DataFrame with validation data
    """
    train_df = pd.DataFrame()
    valid_df = pd.DataFrame()

    for diagnosis in diagnoses_list:

        if baseline:
            train_diagnosis_path = path.join(train_val_path, 'train', diagnosis + '_baseline.tsv')

        else:
            train_diagnosis_path = path.join(train_val_path, 'train', diagnosis + '.tsv')

        valid_diagnosis_path = path.join(train_val_path, 'validation', diagnosis + '_baseline.tsv')

        train_diagnosis_df = pd.read_csv(train_diagnosis_path, sep='\t')
        valid_diagnosis_df = pd.read_csv(valid_diagnosis_path, sep='\t')

        train_df = pd.concat([train_df, train_diagnosis_df])
        valid_df = pd.concat([valid_df, valid_diagnosis_df])

    train_df.reset_index(inplace=True, drop=True)
    valid_df.reset_index(inplace=True, drop=True)

    return train_df, valid_df


def load_data(train_val_path, diagnoses_list, split, n_splits=None, baseline=True, data_path='linear'):

    train_df = pd.DataFrame()
    valid_df = pd.DataFrame()

    if n_splits is None:
        train_path = path.join(train_val_path, 'train')
        valid_path = path.join(train_val_path, 'validation')

    else:
        train_path = path.join(train_val_path, 'train_splits-' + str(n_splits),
                               'split-' + str(split))
        valid_path = path.join(train_val_path, 'validation_splits-' + str(n_splits),
                               'split-' + str(split))

    if data_path in ['mni', 'dartel']:
        train_path = path.join(train_path, 'SPM')
        valid_path = path.join(valid_path, 'SPM')

    print("Train", train_path)
    print("Valid", valid_path)

    for diagnosis in diagnoses_list:

        if baseline:
            train_diagnosis_path = path.join(train_path, diagnosis + '_baseline.tsv')
        else:
            train_diagnosis_path = path.join(train_path, diagnosis + '.tsv')

        valid_diagnosis_path = path.join(valid_path, diagnosis + '_baseline.tsv')

        train_diagnosis_df = pd.read_csv(train_diagnosis_path, sep='\t')
        valid_diagnosis_df = pd.read_csv(valid_diagnosis_path, sep='\t')

        train_df = pd.concat([train_df, train_diagnosis_df])
        valid_df = pd.concat([valid_df, valid_diagnosis_df])

    train_df.reset_index(inplace=True, drop=True)
    valid_df.reset_index(inplace=True, drop=True)

    return train_df, valid_df