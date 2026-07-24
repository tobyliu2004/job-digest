// Offline keyword extractor. Scans a job description's text for known tech
// terms (no API, nothing leaves your browser) and, if you've saved your resume
// text, splits them into "you have this" vs "missing".
//
// Matching notes:
//  * Multi-word / punctuated terms (C++, CI/CD, Node.js, REST API) are matched
//    with custom boundaries so "+" and "#" and "." don't break them.
//  * Short/ambiguous terms (C, R, Go) are matched CASE-SENSITIVELY against the
//    original text, so the language "Go" isn't triggered by the verb "go".

(function () {
  // name, category, aliases, cs (case-sensitive — for short ambiguous terms)
  const DICT = [
    // Languages
    ["Python", "Language", ["python"]],
    ["JavaScript", "Language", ["javascript", "js", "es6"]],
    ["TypeScript", "Language", ["typescript", "ts"]],
    ["Java", "Language", ["java"]],
    ["C++", "Language", ["c\\+\\+", "cpp"]],
    ["C#", "Language", ["c#", "c-sharp", "csharp"]],
    ["C", "Language", ["C"], true],
    ["Go", "Language", ["Go", "Golang", "golang"], true],
    ["Rust", "Language", ["rust"]],
    ["Ruby", "Language", ["ruby"]],
    ["PHP", "Language", ["php"]],
    ["Swift", "Language", ["swift"]],
    ["Kotlin", "Language", ["kotlin"]],
    ["Scala", "Language", ["scala"]],
    ["R", "Language", ["R"], true],
    ["SQL", "Language", ["sql"]],
    ["Bash", "Language", ["bash", "shell scripting", "shell"]],
    ["MATLAB", "Language", ["matlab"]],
    ["Perl", "Language", ["perl"]],

    // Frontend
    ["React", "Frontend", ["react", "react.js", "reactjs"]],
    ["Angular", "Frontend", ["angular"]],
    ["Vue", "Frontend", ["vue", "vue.js", "vuejs"]],
    ["Next.js", "Frontend", ["next.js", "nextjs"]],
    ["Redux", "Frontend", ["redux"]],
    ["HTML", "Frontend", ["html", "html5"]],
    ["CSS", "Frontend", ["css", "css3"]],
    ["Tailwind", "Frontend", ["tailwind"]],
    ["Sass", "Frontend", ["sass", "scss"]],

    // Backend
    ["Node.js", "Backend", ["node.js", "nodejs", "node"]],
    ["Express", "Backend", ["express", "express.js"]],
    ["FastAPI", "Backend", ["fastapi"]],
    ["Django", "Backend", ["django"]],
    ["Flask", "Backend", ["flask"]],
    ["Spring", "Backend", ["spring", "spring boot"]],
    ["Rails", "Backend", ["rails", "ruby on rails"]],
    [".NET", "Backend", ["\\.net", "dotnet", "asp.net"]],
    ["GraphQL", "Backend", ["graphql"]],
    ["REST API", "Backend", ["rest api", "restful", "rest"]],
    ["gRPC", "Backend", ["grpc"]],
    ["WebSockets", "Backend", ["websocket", "websockets"]],
    ["Microservices", "Backend", ["microservice", "microservices"]],

    // Data / ML
    ["pandas", "Data/ML", ["pandas"]],
    ["NumPy", "Data/ML", ["numpy"]],
    ["scikit-learn", "Data/ML", ["scikit-learn", "sklearn", "scikit learn"]],
    ["PyTorch", "Data/ML", ["pytorch"]],
    ["TensorFlow", "Data/ML", ["tensorflow"]],
    ["Keras", "Data/ML", ["keras"]],
    ["XGBoost", "Data/ML", ["xgboost"]],
    ["Spark", "Data/ML", ["spark", "pyspark"]],
    ["Hadoop", "Data/ML", ["hadoop"]],
    ["Airflow", "Data/ML", ["airflow"]],
    ["Kafka", "Data/ML", ["kafka"]],
    ["ETL", "Data/ML", ["etl"]],
    ["NLP", "Data/ML", ["nlp", "natural language processing"]],
    ["Computer Vision", "Data/ML", ["computer vision"]],
    ["Deep Learning", "Data/ML", ["deep learning"]],
    ["Machine Learning", "Data/ML", ["machine learning", "ml"]],
    ["LLM", "Data/ML", ["llm", "large language model", "large language models"]],
    ["Pydantic", "Data/ML", ["pydantic"]],

    // Databases
    ["PostgreSQL", "Database", ["postgresql", "postgres"]],
    ["MySQL", "Database", ["mysql"]],
    ["MongoDB", "Database", ["mongodb", "mongo"]],
    ["Redis", "Database", ["redis"]],
    ["SQLite", "Database", ["sqlite"]],
    ["DynamoDB", "Database", ["dynamodb"]],
    ["Cassandra", "Database", ["cassandra"]],
    ["Elasticsearch", "Database", ["elasticsearch", "elastic search"]],
    ["Snowflake", "Database", ["snowflake"]],
    ["BigQuery", "Database", ["bigquery"]],

    // Cloud / DevOps
    ["AWS", "Cloud/Infra", ["aws", "amazon web services"]],
    ["Azure", "Cloud/Infra", ["azure"]],
    ["GCP", "Cloud/Infra", ["gcp", "google cloud"]],
    ["Docker", "Cloud/Infra", ["docker"]],
    ["Kubernetes", "Cloud/Infra", ["kubernetes", "k8s"]],
    ["Terraform", "Cloud/Infra", ["terraform"]],
    ["Jenkins", "Cloud/Infra", ["jenkins"]],
    ["GitHub Actions", "Cloud/Infra", ["github actions"]],
    ["CI/CD", "Cloud/Infra", ["ci/cd", "cicd", "continuous integration", "continuous deployment"]],
    ["Git", "Cloud/Infra", ["git", "github", "gitlab", "version control"]],
    ["Linux", "Cloud/Infra", ["linux", "unix"]],
    ["nginx", "Cloud/Infra", ["nginx", "openresty"]],
    ["Lambda", "Cloud/Infra", ["lambda", "serverless"]],

    // Concepts
    ["Data Structures & Algorithms", "Concept", ["data structures", "algorithms", "dsa"]],
    ["Object-Oriented Programming", "Concept", ["object-oriented", "object oriented", "oop"]],
    ["Distributed Systems", "Concept", ["distributed systems", "distributed system"]],
    ["System Design", "Concept", ["system design"]],
    ["Agile", "Concept", ["agile", "scrum"]],
    ["TDD", "Concept", ["tdd", "test-driven", "test driven"]],
    ["Unit Testing", "Concept", ["unit test", "unit testing", "unit tests"]],
    ["Integration Testing", "Concept", ["integration test", "integration testing"]],
    ["Concurrency", "Concept", ["concurrency", "multithreading", "concurrent"]],
    ["Caching", "Concept", ["caching", "cache"]],
    ["Authentication", "Concept", ["authentication", "oauth", "jwt", "auth"]],
    ["Design Patterns", "Concept", ["design patterns"]],
  ];

  const BOUND = "[^A-Za-z0-9+#.]";

  function makeRe(alias, cs) {
    // alias may already contain escaped regex (e.g. c\+\+); leave those, escape rest.
    const body = /\\/.test(alias)
      ? alias
      : alias.replace(/[.*+?^${}()|[\]]/g, "\\$&");
    return new RegExp(`(?:^|${BOUND})(?:${body})(?:${BOUND}|$)`, cs ? "g" : "gi");
  }

  function count(text, aliases, cs) {
    let n = 0;
    for (const a of aliases) {
      const m = text.match(makeRe(a, cs));
      if (m) n += m.length;
    }
    return n;
  }

  // Returns [{name, category, count}] for terms found in the description text.
  function extract(text) {
    const found = [];
    for (const [name, category, aliases, cs] of DICT) {
      const n = count(text, aliases, !!cs);
      if (n > 0) found.push({ name, category, count: n });
    }
    return found.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }

  // Split found keywords into what your saved resume already covers vs not.
  function splitByResume(found, resumeText) {
    const have = [];
    const missing = [];
    for (const kw of found) {
      const entry = DICT.find((d) => d[0] === kw.name);
      const aliases = entry ? entry[2] : [kw.name];
      const cs = entry ? !!entry[3] : false;
      const inResume = resumeText && count(resumeText, aliases, cs) > 0;
      (inResume ? have : missing).push(kw);
    }
    return { have, missing };
  }

  window.JobKeywords = { extract, splitByResume };
})();
