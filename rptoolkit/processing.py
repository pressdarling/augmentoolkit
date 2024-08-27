import random
import traceback
from augmentoolkit.generation_functions.engine_wrapper_class import EngineWrapper
from augmentoolkit.utils.write_output_to_file import write_output_to_file
from rptoolkit.steps import API_KEY_A, API_KEY_B, BASE_URL_A, BASE_URL_B, CONCURRENCY_LIMIT, LOGICAL_MODEL_A, LOGICAL_MODEL_B, MODE_A, MODE_B, OUTPUT_FOLDER, chunking_algorithm, count_tokens, extract_charname, extract_features, fix_text, generate_emotion_constrained, generate_emotion_from_text, generate_scene_card, generate_story, is_story_awesome, is_story_ok, make_id, naive_combine_features, obj_conf, rate_story, validate_generation, validate_length_callback, validate_not_none, validate_rating_keys_presence, validate_repetition_callback, write_final_dataset_files


import nltk
from tqdm import asyncio as tqdmasyncio


import asyncio
import glob
import logging
import os
import sys
import time


async def generate_data(chunk: str, engine_wrapper: EngineWrapper, engine_wrapper_large: EngineWrapper, stories, idx):
    # NOTE Generate emotions, or pick
    data = chunk
    try:
        if obj_conf["SYSTEM"]["PICK_EMOTION"]:
            data = await generate_emotion_from_text(chunk=chunk, engine_wrapper=engine_wrapper, idx=idx)
            if data:
                data["emotion"] = data["emotion"].split("\n")[0]
            if not data:
                print(f"Emotion {idx} failed checks.")
                return None, None, None
        else:
            data = await generate_emotion_constrained(chunk, engine_wrapper, idx)
            print(data)
        data = await extract_features(data, engine_wrapper, idx)

        # return

        # return chosen_emotion, features, chunk, "" # DEBUG
        user_tags = obj_conf["SYSTEM"]["TAGS"]

        data = naive_combine_features(data, user_tags) # programmatically naively combine the features together

        data = await generate_scene_card(data, engine_wrapper, idx)
        charname = extract_charname(data["scene_card"])
        return
        
        outs = await generate_story(input_data=data, engine_wrapper=engine_wrapper_large, charname=charname, idx=idx)
        data, truncated = outs

        if not truncated:
            if not data:
                raise Exception("Story generation failed. Story was None. Charnames failed to extract.")
            # story = story # this is to prevent the final data from having crappy endings like "we wonder where this will take us" or whatever, GPT-ism endings are corny af. TODO consider increasing to -4. It should always be even to avoid ending on a user message.
            # TODO maybe re-add

        data = await rate_story(data, engine_wrapper, idx)
        data["id"] = idx
        data["charname"] = charname
        stories.append(data)
    except Exception as e:
        print(f"\n\n\nREALLY BAD EXCEPTION ENCOUNTERED: {e}")
        print("Cutting losses and moving on to the next chunk.")
        traceback.print_exc()


async def main():
    # NOTE Load the source texts
    INPUT_FOLDER = obj_conf["PATH"]["INPUT"]
    start_time = time.time()
    print("Begun")

    # Set up rate-limit-conscious functions
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    async def run_task_with_limit(task):
        async with semaphore:
            return await task


    extension = ".txt"

    path = f"{INPUT_FOLDER}/*" + extension
    source_texts = glob.glob(path)

    # NOTE Initialize the Engine (or API client)
    engine_wrapper = EngineWrapper(
        model=LOGICAL_MODEL_A,
        api_key=API_KEY_A,
        base_url=BASE_URL_A,
        mode=MODE_A,
    )

    engine_wrapper_large = EngineWrapper(
        model=LOGICAL_MODEL_B,
        api_key=API_KEY_B,
        base_url=BASE_URL_B,
        mode=MODE_B,
    )

    # NOTE Tokenize and chunk text
    nltk.download("punkt")

    # any HF path to a transformer model will do, as long as it has a tokenizer

    sentence_chunks = []
    for source_text in source_texts:
        sentence_chunks += chunking_algorithm(source_text)

    conversions = [("  ", " ")]


    paragraphs_processed = [
            {"chunk": fix_text(conversions, seq["chunk"]), "source": seq["source"]} for seq in sentence_chunks
        ]
    if obj_conf["SYSTEM"]["USE_SUBSET"]:
        paragraphs_processed = paragraphs_processed[:obj_conf["SYSTEM"]["SUBSET_SIZE"]]


    logging.info("\n\nParagraphs have been processed and chunked.\n\n")
    if len(paragraphs_processed) > 0:
        logging.info(f"First chunk: {paragraphs_processed[0]}\n\n")
    else:
        logging.info("No paragraphs found.")
        sys.exit(1)

    # NOTE Generate the data
    story_data = []
    data_generations_tasks = [generate_data(chunk=chunk, engine_wrapper=engine_wrapper, engine_wrapper_large=engine_wrapper_large, stories=story_data, idx=idx) for idx, chunk in enumerate(paragraphs_processed)]
    coroutines = [run_task_with_limit(task) for task in data_generations_tasks]
    for future in tqdmasyncio.tqdm.as_completed(coroutines):
        await future

    minimally_ok_stories = [story for story in story_data if is_story_ok(story)]
    highly_rated_stories = [story for story in story_data if is_story_awesome(story)]

    # NOTE Write the output to file using JSON
    os.makedirs(f"{OUTPUT_FOLDER}/final_outputs", exist_ok=True)
    write_final_dataset_files(story_data, "full_stories_list")
    write_final_dataset_files(minimally_ok_stories, "good_and_above_stories_list")
    write_final_dataset_files(highly_rated_stories, "incredible_stories_list")

    print("\n\n\n================== ALL DATA WRITTEN!! HERE ARE YOUR STATS: ==================\n")
    print(f"Total stories generated: {len(story_data)}")
    print(f"Stories that are at least 'good' across the board: {len(minimally_ok_stories)}")
    print(f"Stories that are highly rated by the AI: {len(highly_rated_stories)}")
    total_tokens_of_stories = sum([count_tokens(story["story"]) for story in story_data])
    print("Total tokens of all stories (roughly equivalent to the number of training tokens): ", total_tokens_of_stories)
    print(f"Time taken: {time.time() - start_time} seconds")
    print("ShareGPT export is created, and the full dataset is also available in the final_outputs folder.")
    if len(story_data) == 0:
        print("Hmm... No stories were generated. Check the logs for more information, and consider creating an issue if this is unexpected. If you do make an issue, please include your input data and the logs!")
    else:
        print("Enjoy training your model!")
    print("\n\n\n=============================================================================\n\n\n")
    
asyncio.run(main())