.PHONY: run test clean

run:
	python src/main.py --vfs vfs/deep.zip --log shell_log.csv --script scripts/start_script.txt

test:
	python -m pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
