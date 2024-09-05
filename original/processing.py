import sys
import os

from augmentoolkit.generation_functions.process_multiturn_functions import extract_conversation
import augmentoolkit.utils.create_pretraining_set
import augmentoolkit.utils.sentence_chunking_algorithm
from augmentoolkit.utils.parse_bool import parse_bool
# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))
# Change the current working directory to the script directory
os.chdir(script_dir)
# Add the script directory to the Python path
sys.path.append(script_dir)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import asyncio
import traceback

import augmentoolkit.utils.group_by_text

async def main():
    # NOTE NOTEBOOK SETTINGS AND CONSTANTS (some script file constants are in generation_functions/constants.py)

    import logging
    import yaml
    import glob
    from original import steps
    config_path = os.environ["CONFIG_PATH"]
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if not os.path.exists(config["PATH"]["OUTPUT"]):
        os.makedirs(config["PATH"]["OUTPUT"])

    if (
        not config["SYSTEM"]["COMPLETION_MODE"]
        and config["SYSTEM"]["MODE"] == "aphrodite"
    ):
        raise Exception("Aphrodite engine mode MUST use completion prompts!")

    LOGICAL_MODEL = config["API"]["LOGICAL_MODEL"]

    LARGE_LOGICAL_MODEL = config["API"]["LARGE_LOGICAL_MODEL"]

    DOUBLE_CHECK_COUNTER = int(config["SYSTEM"][
        "DOUBLE_CHECK_COUNTER"
    ])  # Set to 1 to check outputs only once; set to 2 to check twice; set to 3 to check thrice, etc. Set to 0 to break everything in vet_question_loop() and elsewhere. Set to -1 and cause the universe to implode?

    USE_SUBSET = parse_bool(config["SYSTEM"][
        "USE_SUBSET"
    ])  # Set to True if you want to use only a small subset of the text, to test whether it plays nicely with the current setup of the notebook

    SUBSET_SIZE = int(config["SYSTEM"]["SUBSET_SIZE"])  # Set to the number of chunks you want to use if you're using a subset. If you're not using a subset, this will be ignored.

    USE_FILENAMES = parse_bool(config["SYSTEM"][
        "USE_FILENAMES"
    ])  # Turn on if you want the model to use the names of your files as additional context (this is what original Augmentoolkit does). Useful if you have a small number of large input files grouped by subject matter, IE books. Turn off if you have a large number of files with meaningless names.

    CONCURRENCY_LIMIT = int(config["SYSTEM"][
        "CONCURRENCY_LIMIT"
    ])  # Adjust this number based on the rate limit constraints of your api

    API_KEY = config["API"]["API_KEY"]

    BASE_URL = config["API"][
        "BASE_URL"
    ]  # Augmentoolkit-API mode should also be compatible with any other API provider that accepts OAI-style requests

    COMPLETION_MODE = parse_bool(config["SYSTEM"]["COMPLETION_MODE"])

    MODE = config["SYSTEM"]["MODE"]

    LOG_LEVEL = logging.INFO

    INPUT_FOLDER = config["PATH"]["INPUT"]

    CONVERSATION_INSTRUCTIONS = config["SYSTEM"][
        "CONVERSATION_INSTRUCTIONS"
    ]

    # Create pretraining set from raw inputs (pretrain first, then instruct tune)
    augmentoolkit.utils.create_pretraining_set.create_pretraining_set(
        INPUT_FOLDER, os.path.join(config["PATH"]["OUTPUT"], "pretraining.json")
    )

    PHASE_INDEX = int(config["PHASE"]["PHASE_INDEX"])

    WORK_IN_PHASES = parse_bool(config["PHASE"]["WORK_IN_PHASES"])
    
    SKIP_FILTER_CHUNKS = parse_bool(config["SKIP"]["FILTER_CHUNKS"])
    
    CHUNK_SIZE = config["SYSTEM"]["CHUNK_SIZE"]
    
    print("Pretraining set created.")

    extensions = [".txt", ".md"]
    
    print(f"\n\n\nUSE FILENAMES: {USE_FILENAMES}")

    source_texts = []
    for extension in extensions:
      path = f"{INPUT_FOLDER}/**/*" + extension
      source_texts = source_texts + glob.glob(path, recursive=True)

    if source_texts:
        print(source_texts)
    else:
        print(f"No source texts found in: {INPUT_FOLDER}")

    # ## Below: Defines and imports functions that you will probably use no matter what cells in the script you choose to run:

    print(
        "\n\n\nIMPORTANT NOTE! Augmentoolkit prints a lot of stuff when it runs. Including tracebacks caused by model errors. Most errors are the result of the models, not the code, and any tracebacks you see were almost certainly handled. So: don't panic! You're gonna make it! Alright that's the end of this PSA. Happy dataset generation!\n\n\n"
    )

    
    import uuid

    # This is in no way best practices, but all my prompts being searchable and separate files is a good way to make my life easier.
    import pkgutil
    import importlib
    import sys
    from tqdm import asyncio as tqdmasyncio
    import asyncio

    # Set up rate-limit-conscious functions
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def run_task_with_limit(task):
        async with semaphore:
            # Run your task here
            return await task

    # We have to define this up here so that two-step generation works, you'll see later.
    multi_turn_convs_info_dir = (
        config["PATH"]["OUTPUT"] + "/multi_turn_convs_info"
    )  # we generate all the information fed to the multiturn prompt, and generate the actual multiturn prompt, separately; since every step but the last is capable of being done by a 13b

    sys.path.append("./generation_functions")
    sys.path.append("./control_flow_functions")

    import augmentoolkit.generation_functions as generation_functions  # This is the package directory
    from augmentoolkit.generation_functions.engine_wrapper_class import EngineWrapper

    engine_wrapper = EngineWrapper(
        model=LOGICAL_MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        mode=MODE,
        # quantization="gptq" # modify if you want to do stuff with the aphrodite branch
    )
    
    engine_wrapper_large = EngineWrapper(
        model=LARGE_LOGICAL_MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        mode=MODE,
        # quantization="gptq" # modify if you want to do stuff with the aphrodite branch
    )
    
    import re
    from tqdm import tqdm

    sentence_chunks = []
    for source_text in source_texts:
        sentence_chunks += augmentoolkit.utils.sentence_chunking_algorithm.sentence_chunking_algorithm(
            source_text, CHUNK_SIZE
        )

    conversions = [("\n", " "), ("  ", " ")]

    paragraphs_processed = [
        {
            "paragraph": steps.fix_text(conversions, seq["paragraph"]), 
            "metadata": seq["metadata"]
        }
        for seq in sentence_chunks
    ]

    len(paragraphs_processed)

    paragraphs_processed[0]

    print(paragraphs_processed[:3])

    import json
    
    from tqdm import tqdm
    import asyncio


    if SKIP_FILTER_CHUNKS:
        print("Skipping chunk filtering")
        filtered_worthy_for_questions = paragraphs_processed[:SUBSET_SIZE]
    else:
        # Create directory if it doesn't exist
        output_dir = config["PATH"]["OUTPUT"] + "/worthy_for_questions"
        os.makedirs(output_dir, exist_ok=True)
        
        # Determine which paragraphs are worthy of making questions from
        judged_worthy_for_questions = []

        await steps.filter_all_questions(
            paragraphs_processed,
            judged_worthy_for_questions,
            engine_wrapper,
            take_subset=USE_SUBSET,
            subset_size=SUBSET_SIZE,
            use_filenames=False,
            rtwl=run_task_with_limit,
            completion_mode=COMPLETION_MODE,
            logging_level=LOG_LEVEL,
        )

        filtered_worthy_for_questions = steps.filter_and_graph(
            judged_worthy_for_questions
        )
        
        print("Converting generations to training data")
        steps.convert_logging_to_dataset(input_pth=os.path.join("judge_paragraph_generations", "intermediate_generations"), output_pth="judge_paragraph_generations")

    print(filtered_worthy_for_questions[0])
    
    # PHASE 0 END
    print("\n\nCOMPLETED PHASE 0")
    if WORK_IN_PHASES and PHASE_INDEX == 0:
        sys.exit(0)
    
    #####

    # control flow
    import json
    
    import glob

    # Directory for QA tuples
    qa_tuples_dir_unchecked = config["PATH"]["OUTPUT"] + "/qatuples_raw"
    if not os.path.exists(qa_tuples_dir_unchecked):
        os.makedirs(qa_tuples_dir_unchecked)

    generated_qa_dicts = []  # tuple list of qa tuples that have been judged good

    # Attempt to initialize filtered_worthy_for_questions
    try:
        _ = filtered_worthy_for_questions
    except NameError:
        filtered_worthy_for_questions = []

    if not filtered_worthy_for_questions:
        # Load all files in the qa_tuples_dir if filtered_worthy_for_questions is not initialized
        existing_files = glob.glob(os.path.join(qa_tuples_dir_unchecked, "*.json"))
        for file_path in existing_files:
            with open(file_path, "r") as file:
                qa_tuple = tuple(json.load(file))
                print(f"Loaded {file}")
            generated_qa_dicts.append(qa_tuple)
    else:
        tasks = [
            steps.generate_qadicts_from_para(
                idx,
                para,
                engine_wrapper_large=engine_wrapper_large,
                generated_qa_dicts=generated_qa_dicts,
            )
            for idx, para in enumerate(filtered_worthy_for_questions)
        ]
        limited_tasks_qgen = [run_task_with_limit(task) for task in tasks]
        for future in tqdmasyncio.tqdm.as_completed(limited_tasks_qgen):
            await future
    
    # PHASE 1 END
    print("COMPLETED PHASE 1")
    if WORK_IN_PHASES and PHASE_INDEX == 1:
        print("EXITING DUE TO config.yaml SETTINGS AROUND PHASES; SET TO ONLY EXECUTE PHASE 1 RIGHT NOW")
        sys.exit(0)
    ####
    
    vetted_qa_dicts = []
    qa_dicts_dir_checked = os.path.join(config["PATH"]["OUTPUT"], "qatuples_filtered")
    if not os.path.exists(qa_dicts_dir_checked):
        os.makedirs(qa_dicts_dir_checked)
    
    print(generated_qa_dicts[0])
    
    tasks = [
        steps.vet_question_loop(
            question_answer_dict,
            question_group_id=question_answer_dict['question_group_id'],
            engine_wrapper=engine_wrapper,
            qa_dicts_dir=qa_dicts_dir_checked,
            vetted_qa_dicts=vetted_qa_dicts,
            double_check_counter=DOUBLE_CHECK_COUNTER,
            completion_mode=COMPLETION_MODE,
            logging_level=LOG_LEVEL,
        ) for question_answer_dict in generated_qa_dicts
    ]
    limited_tasks_q_validation = [run_task_with_limit(task) for task in tasks]
    for future in tqdmasyncio.tqdm.as_completed(limited_tasks_q_validation):
            await future
                
    
    if WORK_IN_PHASES and PHASE_INDEX == 2:
        print("EXITING DUE TO config.yaml SETTINGS AROUND PHASES; SET TO ONLY EXECUTE PHASE 2 RIGHT NOW")
        sys.exit(0)

    print(
        "-------------- QUESTIONS CREATED ------------- STATS SO FAR (may be wrong if run was continued from interruption):"
    )
    nones = list(filter(lambda x: x is None, vetted_qa_dicts))
    print(f"Nones: {len(nones)}")
    print(f"Non-nones: {len(vetted_qa_dicts) - len(nones)}")
    print(f"Total: {len(vetted_qa_dicts)}")
    # filter out all None values
    vetted_qa_dicts = [qa for qa in vetted_qa_dicts if qa is not None]
    print("---------------- ONTO REVISION ------------------")

    # Assuming vetted_qa_tuples is a list that might or might not exist
    tasks = [
        steps.repair_qatuple_context( # NOTE PROBLEM in that things that this writes, do not have enough items in the tuple
            idx,
            tup,
            engine_wrapper_large,
            vetted_qa_dicts,
        )
        for idx, tup in enumerate(vetted_qa_dicts)
    ]
    limited_tasks_qcorrection = [run_task_with_limit(task) for task in tasks]
    for future in tqdmasyncio.tqdm.as_completed(limited_tasks_qcorrection):
        await future

    print("-------------- QUESTIONS REVISED ------------- STATS SO FAR:")
    nones = list(filter(lambda x: x is None, vetted_qa_dicts))
    print(f"Nones: {len(nones)}")
    print(f"Non-nones: {len(vetted_qa_dicts) - len(nones)}")
    print(f"Total: {len(vetted_qa_dicts)}")
    # filter out all None values
    vetted_qa_dicts = [qa for qa in vetted_qa_dicts if qa is not None]
    print("---------------- ONTO EXAMPLES GENERATION-------------------")

    qa_dicts_by_text = augmentoolkit.utils.group_by_text.group_by_text(vetted_qa_dicts)
    
    print("Creating question generation training data...")
    steps.convert_revised_questions_to_question_generation_training(qa_dicts_by_text=qa_dicts_by_text, use_filenames=USE_FILENAMES)

    multi_turn_convs = []

    tasks = [
        steps.create_conversation(
            idx,
            info,
            engine_wrapper_large,
            multi_turn_convs,
        )
        for idx, info in enumerate(qa_dicts_by_text)
    ]
    limited_tasks_convwriting = [run_task_with_limit(task) for task in tasks]
    for future in tqdmasyncio.tqdm.as_completed(limited_tasks_convwriting):
        await future

    print("Converting conversational data generations to training data")
    steps.convert_logging_to_dataset(input_pth=os.path.join("multi_turn_convs", "intermediate_generations"), output_pth="multi_turn_convs")

    # Yay! Now you have a dataset!

    
    import json

    # Make ShareGPT-format dataset (I think, still need verification it actually works)
    steps.convert_directory_to_list(
        os.path.join(config["PATH"]["OUTPUT"],"multi_turn_convs", "saved_readable_generations")
    )
    
    with open(config["PATH"]["OUTPUT"] + "/master_list.jsonl", "r") as f:
        data = [json.loads(line) for line in f]

    # For curiosity's sake, you can find out how many lines of dialogue you generated
    # TODO add token count
    gpt_turns = 0        
    for dict in data:
        conv = dict['conversation']
        turns = extract_conversation(conv)
        for turn in turns:
            if "AI" in turn[0]:
                gpt_turns += 1


    print(f"Total GPT turns: {gpt_turns}")
    print("COMPLETED FINAL PHASE")


asyncio.run(main())