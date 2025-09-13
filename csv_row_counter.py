import csv

def count_csv_rows(file_path):
    with open(file_path, 'r') as file:
        reader = csv.reader(file)
        row_count = sum(1 for row in reader)
    return row_count

file_path = '/Users/jangom2ok/work/tmp/youtube/zatsukuriwakaru/index_a.csv'  # CSVファイルのパスを指定
row_count = count_csv_rows(file_path)
print("行数:", row_count)
