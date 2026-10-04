# Базовая модель против адаптера

Адаптер: `models\adapter_all_layers`. Генерация жадная, до 40 токенов.

## 1. serialize SSE retry field when value is zero

**База:**

> ```json / {"type": "refactor", "breaking": false} / ```

**Адаптер:**

> {"type": "fix", "breaking": false}

## 2. fix grammar 'does not supports' -> 'does not support' in node22 ESM sample

**База:**

> ```json / {"type": "fix", "breaking": false} / ```

**Адаптер:**

> {"type": "docs", "breaking": false}

## 3. use Object.hasOwn

**База:**

> ```json / { /   "type": "feat", /   "breaking": false / } / ```

**Адаптер:**

> {"type": "refactor", "breaking": false}

## 4. add override mimetype validation coverage

**База:**

> ```json / {"type": "feat", "breaking": false} / ```

**Адаптер:**

> {"type": "test", "breaking": false}

## 5. update multerExceptions constant

**База:**

> ```json / {"type": "feat", "breaking": false} / ```

**Адаптер:**

> {"type": "chore", "breaking": false}
