# Product QA evaluation report
- n: **100**
- overall_accuracy: **0.770**

## Accuracy by question type
- **barcode_lookup**: 1.000
- **category_count**: 0.000
- **category_under_price**: 1.000
- **cheapest_in_category**: 0.375
- **location_lookup**: 1.000
- **most_expensive_in_category**: 0.250
- **price_lookup**: 1.000

## Sample failures (first 10)
### category_count_0 (category_count)
**Q:** How many products are in the category Christmas?

**Expected:** `{'category': 'Christmas', 'count': 431}`

**Answer:** There are 5 products in the category "Christmas".

### category_count_1 (category_count)
**Q:** How many products are in the category Candle Stands & Lanterns?

**Expected:** `{'category': 'Candle Stands & Lanterns', 'count': 40}`

**Answer:** There are 5 products in the category "Candle Stands & Lanterns".

### category_count_2 (category_count)
**Q:** How many products are in the category Baskets?

**Expected:** `{'category': 'Baskets', 'count': 108}`

**Answer:** There are 5 products in the category "Baskets".

### category_count_3 (category_count)
**Q:** How many products are in the category Balloons?

**Expected:** `{'category': 'Balloons', 'count': 48}`

**Answer:** There are 5 products in the category "Balloons".

### category_count_4 (category_count)
**Q:** How many products are in the category Seasonal Items?

**Expected:** `{'category': 'Seasonal Items', 'count': 19}`

**Answer:** 

### category_count_5 (category_count)
**Q:** How many products are in the category Soft Toys?

**Expected:** `{'category': 'Soft Toys', 'count': 22}`

**Answer:** There are 5 products in the category "Soft Toys".

### category_count_6 (category_count)
**Q:** How many products are in the category Garden Furniture?

**Expected:** `{'category': 'Garden Furniture', 'count': 8}`

**Answer:** There are 5 products in the category "Garden Furniture".

### category_count_7 (category_count)
**Q:** How many products are in the category Funeral & Graves?

**Expected:** `{'category': 'Funeral & Graves', 'count': 71}`

**Answer:** There are 5 products in the category "Funeral & Graves".

### category_count_8 (category_count)
**Q:** How many products are in the category Candles & Lights?

**Expected:** `{'category': 'Candles & Lights', 'count': 108}`

**Answer:** There are 5 products in the category "Candles & Lights".

### category_count_9 (category_count)
**Q:** How many products are in the category Ribbons & Bows?

**Expected:** `{'category': 'Ribbons & Bows', 'count': 354}`

**Answer:** There are 5 products in the category "Ribbons & Bows".

