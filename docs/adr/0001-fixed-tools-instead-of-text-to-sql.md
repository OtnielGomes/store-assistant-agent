# Fixed parameterized tools instead of LLM-generated SQL

The Assistant reads Facts only through a fixed set of tools (such as catalog search and order lookup), each backed by a parameterized SQL query; the LLM chooses the tool and its arguments but never writes SQL. Text-to-SQL was rejected because it can hallucinate columns, skip the mandatory filters (active Products, Active Promotions, order verification), and pull Customer phone numbers and e-mails into the model context; fixed tools make every Fact the Assistant states traceable to a known query and keep personal data out of the prompt.
