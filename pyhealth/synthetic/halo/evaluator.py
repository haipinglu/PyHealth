import itertools

from matplotlib import pyplot as plt
import numpy as np

from typing import Callable, Dict, List
from pyhealth.synthetic.halo.generator import Generator
from pyhealth.synthetic.halo.processor import Processor
from pyhealth.utils import print_dict
from sklearn.metrics import r2_score
from tqdm import tqdm

from pyhealth.datasets.base_ehr_dataset import BaseEHRDataset

class Evaluator:

    # used to access the output of the evaluate(...) function
    SOURCE_STATS = "source_stats"
    SYNTHETIC_STATS = "synthetic_stats"
    PLOT_PATHS = "plot_paths"

    RECORD_LEN_MEAN = "Record Length Mean"
    RECORD_LEN_STD = "Record Length Standard Deviation"
    VISIT_LEN_MEAN = "Visit Length Mean"
    VISIT_LEN_STD = "Visit Length Standard Deviation"
    TEMPORAL_MEAN = "Inter-visit time Mean"
    TEMPORAL_STD = "Inter-visit time Standard Deviation"
    AGGREGATE = "Aggregate"

    RECORD_CODE_PROB = "Per Record Code Probabilities"
    VISIT_CODE_PROB = "Per Visit Code Probabilities"
    RECORD_BIGRAM_PROB = "Per Record Bigram Probabilities"
    VISIT_BIGRAM_PROB = "Per Visit Bigram Probabilities"
    RECORD_SEQUENTIAL = "Per Record Sequential Visit Bigram Probabilities"
    VISIT_SEQUENTIAL = "Per Visit Sequential Visit Bigram Probabilities"
    PROBABILITIES = "Probabilities"
    LABEL_PROBABILITIES = "Label Probabilities"

    PROBABILITY_DENSITIES = [
        RECORD_CODE_PROB,
        VISIT_CODE_PROB,
        RECORD_BIGRAM_PROB,
        VISIT_BIGRAM_PROB,
        RECORD_SEQUENTIAL,
        VISIT_SEQUENTIAL
    ]

    def __init__(
            self,
            generator: Generator,
            processor: Processor,
        ):
        self.generator = generator
        self.processor = processor

        # all ones, 1 index longer than any other label
        self.ALL_LABELS = tuple(np.ones(self.processor.label_vector_len + 1))
    
    def default_path_fn(self, plot_type, label_vector):
        label_string = str(tuple(label_vector))
        path = f"./pyhealth_halo_eval_{plot_type}_{label_string}"
        return path.replace('.', '').replace(' ', '').lower()

    def evaluate(self, source, synthetic, compare_label: List = None, get_plot_path_fn: Callable = None, print_overall: bool = True):
        halo_labels, halo_ehr_stats = self.generate_statistics(ehr_dataset=synthetic)
        
        source_as_vectors = self.to_evaluation_format(source)
        train_erh_labels, train_ehr_stats = self.generate_statistics(ehr_dataset=source_as_vectors)
        
        assert halo_labels, "No labels present in HALO Dataset, this is likely because the dataset schema is incorrect."
        assert train_erh_labels, "No labels present in Training Dataset, this is likely because the dataset schema is incorrect."

        if print_overall:
            print("source (train)")
            print_dict(train_ehr_stats[self.ALL_LABELS][self.AGGREGATE])
            print_dict(train_ehr_stats[self.LABEL_PROBABILITIES])
            print("synthetic")
            print_dict(halo_ehr_stats[self.ALL_LABELS][self.AGGREGATE])
            print_dict(halo_ehr_stats[self.LABEL_PROBABILITIES])

        # Plot per-code statistics
        plot_paths = self.generate_plots(train_ehr_stats, halo_ehr_stats, "Source Data", "Synthetic Data", get_plot_path_fn=get_plot_path_fn, compare_labels=compare_label)

        return {self.SOURCE_STATS: train_ehr_stats, self.SYNTHETIC_STATS: halo_ehr_stats, self.PLOT_PATHS: plot_paths}
    
    def to_evaluation_format(self, dataset: BaseEHRDataset) -> List[Dict]:
        """
        computing probability densities is more straighforward on a vector dataset.
        This method processes a pyhealth dataset into the HALO vector format
        """
        
        converted_samples = []
        for batch_ehr, _ in self.processor.get_batch(dataset, self.generator.batch_size):
            converted_sample_batch = self.generator.convert_samples_to_ehr(samples=batch_ehr)    
            converted_samples += (converted_sample_batch)
        
        return converted_samples

    def generate_statistics(self, ehr_dataset) -> Dict:
        """Compute basic statistics and probability densities of code occurrences (unigram, bigram, sequential bigram)"""
        
        # compute all available lables in the dataset
        labels = set()
        for sample in ehr_dataset: labels.add(sample[self.generator.LABEL])

        # used in plot generation later
        dataset_labels = tuple(labels)

        # generate overall statistics
        labels.add(self.ALL_LABELS)

        # collect stats for the current label
        stats = {}
        label_counts = {}
        for label in sorted(list(labels)):
            
            # select the current subset to generate stats for
            ehr_subset = []
            if label != self.ALL_LABELS:
                for sample in ehr_dataset:
                    if sample[self.generator.LABEL] == label:
                        ehr_subset.append(sample)
            else:
                ehr_subset = ehr_dataset

            # compute stats per label
            label_subset = ehr_dataset
            label_counts[label] = len(label_subset)

            label_stats = {}

            # compute aggregate stats
            record_lens = []
            visit_lens = []
            visit_gaps = []
            for sample in label_subset:
                visits = sample[self.generator.VISITS]
                timegap = sample[self.generator.TIME]
                record_lens.append(len(visits))
                visit_lens += [len(v) for v in visits]
                visit_gaps.append(timegap)

            aggregate_stats = {}
            aggregate_stats[self.RECORD_LEN_MEAN] = np.mean(record_lens)
            aggregate_stats[self.RECORD_LEN_STD] = np.std(record_lens)
            aggregate_stats[self.VISIT_LEN_MEAN] = np.mean(visit_lens)
            aggregate_stats[self.VISIT_LEN_STD] = np.std(visit_lens)
            aggregate_stats[self.TEMPORAL_MEAN] = np.mean(visit_lens)
            aggregate_stats[self.TEMPORAL_STD] = np.std(visit_lens)
            label_stats[self.AGGREGATE] = aggregate_stats

            # compute probability densities
            code_stats = {}
            n_records = len(record_lens)
            n_visits = len(visit_lens)
            record_code_counts = {}
            visit_code_counts = {}
            record_bigram_counts = {}
            visit_bigram_counts = {}
            record_sequential_bigram_counts = {}
            visit_sequential_bigram_counts = {}
            for row in label_subset:
                patient_codes = set()
                patient_bigrams = set()
                sequential_bigrams = set()
                for j, visit in enumerate(row[self.generator.VISITS]):
                    v = list(set(visit)) # remove duplicates
                    for c in v:
                        visit_code_counts[c] = 1 if c not in visit_code_counts else visit_code_counts[c] + 1
                        patient_codes.add(c)
                    for cs in itertools.combinations(v,2):
                        cs = list(cs)
                        cs.sort()
                        cs = tuple(cs)
                        visit_bigram_counts[cs] = 1 if cs not in visit_bigram_counts else visit_bigram_counts[cs] + 1
                        patient_bigrams.add(cs)
                    if j > 0:
                        v0 = list(set(row[self.generator.VISITS][j - 1]))
                        for c0 in v0:
                            for c in v:
                                sc = (c0, c)
                                visit_sequential_bigram_counts[sc] = 1 if sc not in visit_sequential_bigram_counts else visit_sequential_bigram_counts[sc] + 1
                                sequential_bigrams.add(sc)
                for c in patient_codes:
                    record_code_counts[c] = 1 if c not in record_code_counts else record_code_counts[c] + 1
                for cs in patient_bigrams:
                    record_bigram_counts[cs] = 1 if cs not in record_bigram_counts else record_bigram_counts[cs] + 1
                for sc in sequential_bigrams:
                    record_sequential_bigram_counts[sc] = 1 if sc not in record_sequential_bigram_counts else record_sequential_bigram_counts[sc] + 1
            record_code_probs = {c: record_code_counts[c]/n_records for c in record_code_counts}
            visit_code_probs = {c: visit_code_counts[c]/n_visits for c in visit_code_counts}
            record_bigram_probs = {cs: record_bigram_counts[cs]/n_records for cs in record_bigram_counts}
            visit_bigram_probs = {cs: visit_bigram_counts[cs]/n_visits for cs in visit_bigram_counts}
            record_sequential_bigram_probs = {sc: record_sequential_bigram_counts[sc]/n_records for sc in record_sequential_bigram_counts}
            visit_sequential_bigram_probs = {sc: visit_sequential_bigram_counts[sc]/(n_visits - len(label_subset)) for sc in visit_sequential_bigram_counts}
            
            code_stats[self.RECORD_CODE_PROB] = record_code_probs
            code_stats[self.VISIT_CODE_PROB] = visit_code_probs
            code_stats[self.RECORD_BIGRAM_PROB] = record_bigram_probs
            code_stats[self.VISIT_BIGRAM_PROB] = visit_bigram_probs
            code_stats[self.RECORD_SEQUENTIAL] = record_sequential_bigram_probs
            code_stats[self.VISIT_SEQUENTIAL] = visit_sequential_bigram_probs
            
            label_stats[self.PROBABILITIES] = code_stats
            stats[label] = label_stats
        label_probs = {l: label_counts[l]/n_records for l in label_counts}
        
        stats[self.LABEL_PROBABILITIES] = label_probs
        
        return dataset_labels, stats
    
    def generate_plots(self, stats_a, stats_b, plot_label_a, plot_label_b, get_plot_path_fn: Callable = None, compare_labels: List = None) -> List[str]:
        """Generate plots"""
        if compare_labels == None:
            compare_labels = [self.ALL_LABELS]

        plot_paths = []
        for label in tqdm(compare_labels, desc="Evalutor: generating label plots"):
            data1 = stats_a[label][self.PROBABILITIES]
            data2 = stats_b[label][self.PROBABILITIES]
            for t in self.PROBABILITY_DENSITIES:
                figure_path = get_plot_path_fn(t, label) if get_plot_path_fn != None else self.default_path_fn(t, label)
                print(f"\nLabel stats {figure_path}:")
                probs1 = data1[t]
                probs2 = data2[t]
                keys = set(probs1.keys()).union(set(probs2.keys()))
                values1 = [probs1[k] if k in probs1 else 0 for k in keys]
                values2 = [probs2[k] if k in probs2 else 0 for k in keys]

                plt.clf()
                r2 = r2_score(values1, values2)
                print(f"{t} r-squared = {r2}")
                plt.scatter(values1, values2, marker=".", alpha=0.66)
                maxVal = min(1.1 * max(max(values1), max(values2)), 1.0)
                # maxVal *= (0.3 if 'Sequential' in t else (0.45 if 'Code' in t else 0.3))
                
                plt.xlim([0,maxVal])
                plt.ylim([0,maxVal])
                plt.title(f"{label} {t}")
                plt.xlabel(plot_label_a)
                plt.ylabel(plot_label_b)
                plt.annotate("r-squared = {:.3f}".format(r2), (0.1*maxVal, 0.9*maxVal))
                
                plt.savefig(figure_path)
                plot_paths.append(figure_path)

        return plot_paths