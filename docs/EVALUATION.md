# Inferra on real data

Synthetic samples show that the pipeline works; they cannot show whether the agents reach the right
answer on data nobody prepared for them. This document records an evaluation on five public Kaggle
datasets, what it found, and what was changed because of it.

## Method

- **Real data, real deployment.** Each dataset was uploaded to the live demo (Render free instance,
  0.1 CPU and 512 MB; Vercel; Neon Postgres) and analyzed by the production agents with a real model.
- **Answers written down first.** Before each run, the expected findings and traps were written down
  from published analyses of the dataset: known effect sizes, row counts, and the mistakes people
  usually make with it.
- **Every run reviewed by a person.** Insights were read against those expectations. A run counted as
  correct only when its numbers matched the reference and it avoided the trap; "verified" alone was
  not enough (see below).
- **Fix, then re-run.** When a run exposed a product problem, it was fixed in a pull request with a
  test that reproduces it, deployed, and the same question was asked again.
- **Reviewed insights published.** The final run of each dataset was reviewed, edited where needed,
  and published as a report, linked from the [README](../README.md#on-real-data).

"Verified" means the deterministic verifier traced every number in an insight to a tool result. It
says nothing about whether the analysis was the right one: an insight can be fully verified and still
answer with the wrong measure, as Online Retail's first revenue ranking did. Both checks are reported
below.

## Results

| Dataset | Question | Verified | Against the reference |
|---|---|---|---|
| Cookie Cats (A/B test, 90,189 players) | Move the first gate from level 30 to 40? | 4/4 | Correct: 7-day retention −0.82 pp (p=0.0016), no offsetting gain |
| Telco Customer Churn (7,043 customers) | What needs fixing before modeling churn? | 5/5 | Correct |
| | Predict who will churn and explain why | 4/4 | Correct: holdout AUC 0.848, contract and tenure lead |
| | Do two-year contracts churn less, after tenure and charges? | 3/3 | Correct: odds ratio 0.158, reported as an association |
| Walmart weekly sales (45 stores) | Forecast the next 12 weeks | 4/4 | Correct, including the holiday-flag trap; exposed a bug that dropped the last week ([#22](https://github.com/SweetVege/inferra/pull/22)) |
| | Do holiday weeks sell more? | 3/3 | Correct: +7.8%, driven by Thanksgiving, 10 independent weeks |
| | Do unemployment, fuel, CPI or temperature explain sales? | 3/3 | Correct only after fixes: holding out whole stores turns R² 0.30 into −0.25 |
| Olist e-commerce (4 tables) | Build a data model | 4/4 | Correct: non-unique review IDs, orders without items, review grain |
| | Do late deliveries hurt reviews? | 3/3 | Correct only after fixes: gap 1.72 points (95% CI 1.68–1.76) with 7 controls |
| | What drives revenue? | 4/4 | Correct, including the incomplete first and last months |
| | Predict 1–2 star reviews | 4/4 | Correct: AUC 0.756 on delivered orders; leakage and timing flagged |
| Online Retail (541,909 rows) | What needs cleaning? | 5/5 | Correct: every count matched published analyses exactly |
| | Which products drive revenue? | 3/3 | Correct only after a fix: a cancelled bulk order ranked third until reversals were netted |
| | Customer retention | 4/4 | Correct: the first cohort's higher retention explained by pre-existing customers |

Every insight in the final runs passed the verifier. Three of the fourteen questions reached the
right answer only after a product fix; the first attempts were verified but wrong or incomplete.

## What the evaluation changed

About twenty pull requests came out of the evaluation. Grouped by what they fixed:

**The verifier rejected correct insights**
- A numeric range shares its unit, so "−1.33 to −0.31 pp" is one claim ([#13](https://github.com/SweetVege/inferra/pull/13)).
- "percentage points" and "percent" spelled out are units ([#14](https://github.com/SweetVege/inferra/pull/14)).
- Reviewer edits to an insight are verified again, and an edit that would break a verified insight is refused ([#16](https://github.com/SweetVege/inferra/pull/16)).

**Real files did not load, or loaded wrong**
- Legacy `.xls` uploads, and a readable error for broken spreadsheets ([#18](https://github.com/SweetVege/inferra/pull/18)).
- A table's grain was taken from a unique measure (weekly sales) instead of store and date ([#23](https://github.com/SweetVege/inferra/pull/23)).
- CSV files that are not UTF-8, which is most Excel exports on Windows ([#34](https://github.com/SweetVege/inferra/pull/34)).
- Dates such as `12/1/2010 8:26` stayed text; they now become dates when every value parses one way, and stay text when month-first and day-first both fit ([#35](https://github.com/SweetVege/inferra/pull/35)).

**Numbers were computed wrong**
- A forecast and a metric check dropped the last week of data stamped once a week ([#22](https://github.com/SweetVege/inferra/pull/22)).
- Joined tables with missing values crashed the regression ([#30](https://github.com/SweetVege/inferra/pull/30)).
- Revenue dropped cancellations but kept the sales they cancelled ([#36](https://github.com/SweetVege/inferra/pull/36)).
- A model no better than predicting the mean was registered without a warning ([#26](https://github.com/SweetVege/inferra/pull/26)).
- Smaller fixes from Cookie Cats and Telco: expected loss shown in percentage points, all calibration bins passed to the agent, regression artifacts named by model and filter ([#12](https://github.com/SweetVege/inferra/pull/12), [#17](https://github.com/SweetVege/inferra/pull/17), [#19](https://github.com/SweetVege/inferra/pull/19), [#20](https://github.com/SweetVege/inferra/pull/20)).

**Agents stopped short**
- Agents suggested checks they could have run, three times in three datasets. They now run any check
  their tools and remaining calls allow, and see how many calls are left ([#25](https://github.com/SweetVege/inferra/pull/25)).
- Explanations stated as fact are tested with a tool call, and called hypotheses only when no tool can
  test them ([#26](https://github.com/SweetVege/inferra/pull/26), [#31](https://github.com/SweetVege/inferra/pull/31)).
- Models can be validated on whole groups held out, such as stores ([#25](https://github.com/SweetVege/inferra/pull/25)).
- Statistical tools read one table, so an effect spread over orders and reviews could not be
  estimated. `create_view` joins tables first, and the plan keeps tables that hold confounders
  ([#28](https://github.com/SweetVege/inferra/pull/28), [#29](https://github.com/SweetVege/inferra/pull/29), [#31](https://github.com/SweetVege/inferra/pull/31)).

**Runs failed on a small instance**
- A model call or step could hang indefinitely; both now have time limits, and the run page shows
  which tool a step is in ([#24](https://github.com/SweetVege/inferra/pull/24)).
- DuckDB sized itself to the host instead of the container and took the service down; it is now
  capped to the container's memory and CPU ([#29](https://github.com/SweetVege/inferra/pull/29)).
- An unexpected error in one tool failed the whole run; it now costs one tool call ([#30](https://github.com/SweetVege/inferra/pull/30)).
- Training took about 40% less time on large tables, with the same holdout scores ([#32](https://github.com/SweetVege/inferra/pull/32)).

## What is not solved

- **The same question can be read two ways.** Two runs of Olist's review question computed the same
  numbers but led with different estimates: one called the 1.72-point gap the total association, the
  other called the 0.38-point gap the cleaner effect. The verifier checks numbers, not which number
  deserves the headline. A person still has to review the framing.
- **The model may know these datasets.** All five are well known, and published analyses may be in
  the model's training data. Results on data nobody has written about may be weaker.
- **Step summaries are not verified.** Insights are checked number by number; the specialists'
  summaries shown on the run page are not, and occasionally contained outside knowledge or causal
  wording.
- **Stopping short is rarer, not gone.** After the fixes, agents still sometimes put a check they
  could have run into "next step" instead of running it.
- **Explanations can be loose.** An agent attributed a late jump in a retention curve to a thin base
  when the seasonal peak explains more of it.

## Limits of this evaluation

- Five datasets and three to four questions each: enough to find recurring problems, not to estimate
  an error rate.
- The reviewer read most results from screenshots of the live deployment, so a few figures in the
  review notes may be misread. The published reports and the README quote figures from the insight
  cards themselves.
- The free hosting tier shaped some fixes (memory caps, faster training). On a larger instance the
  same runs would finish faster but should reach the same answers.
