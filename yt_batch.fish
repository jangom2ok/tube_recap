#!/opt/homebrew/bin/fish

set SRC /path/to/videos.csv
set OUTDIR /path/to/outdir
set BATCH 10

set total (xsv count $SRC)   # レコード数（ヘッダー除く）
set start 0

while test $start -lt $total
  set tmp (mktemp -t yt_batch_XXXX).csv
  xsv slice -s $start -l $BATCH $SRC > $tmp  # ヘッダー保持で安全に抽出

  python yt_summary.py \
    --video-ids-file $tmp \
    --max-videos $BATCH \
    --outdir $OUTDIR \
    --provider openai \
    --model gpt-4.1 \
    --clean-tags \
    --cookies-file $OUTDIR/cookies.txt \
    --use-ytdlp \
    --rps 0.3

  rm $tmp
  set start (math $start + $BATCH)
end
